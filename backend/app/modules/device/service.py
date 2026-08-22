"""단말 서비스.

이 모듈이 devices 테이블을 소유한다. 대상 해석(구역 → MAC 목록)도 여기 책임이다.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence

from sqlalchemy import Select, and_, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.constants import DeviceState
from app.core.scope import VillageScope
from app.errors import ApiError, DeviceAlreadyExists, DeviceNotFound, VillageNotFound
from app.models.device import Device
from app.models.org import Village, Zone
from app.models.system import CurrentConfig
from app.modules.org import service as org_service
from app.mqtt.publisher import MqttPublisher
from app.schemas.device import DeviceCreate, DeviceDetail, DeviceOut, DeviceUpdate
from app.tasks import config_reconcile

log = logging.getLogger(__name__)


def online_cutoff() -> dt.datetime:
    """이 시각보다 최근에 통신했으면 온라인."""
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        seconds=settings.device_online_threshold_sec
    )


def is_online(device: Device, cutoff: dt.datetime) -> bool:
    """온라인 판정.

    최근 통신만으로 판단하지 않는다. LWT 로 OFFLINE 이 확정된 단말은 마지막 통신이
    아무리 최근이어도 이미 끊긴 상태다 — 5분 임계를 기다릴 이유가 없다.
    """
    status = device.last_status or {}
    if status.get("state") == DeviceState.OFFLINE.value:
        return False
    return device.last_seen_at is not None and device.last_seen_at >= cutoff


#: 온라인 판정의 SQL 판. is_online() 과 반드시 같은 규칙이어야 한다.
#: 대시보드도 이걸 쓴다 — 판정이 두 벌이면 타일과 목록이 서로 다른 말을 한다.
def online_clause(cutoff: dt.datetime):
    return and_(
        Device.last_seen_at >= cutoff,
        or_(
            Device.last_status.is_(None),
            Device.last_status["state"].astext != DeviceState.OFFLINE.value,
        ),
    )


def _base_query() -> Select:
    """단말 + 마을명 + 구역명. 목록 화면이 이름을 같이 보여줘야 해서 조인해 둔다."""
    village = aliased(Village)
    zone = aliased(Zone)
    return (
        select(Device, village.name, zone.name)
        .outerjoin(village, Device.village_id == village.id)
        .outerjoin(zone, Device.zone_id == zone.id)
    )


def _scoped(stmt: Select, scope: VillageScope, *, include_unassigned: bool) -> Select:
    """마을 범위 필터.

    미배정(village_id IS NULL)은 어느 마을에도 속하지 않으므로 super_admin 만 본다.
    include_unassigned 는 super_admin 의 '미배정 단말' 섹션에서만 True 가 된다.
    """
    if scope.all_villages:
        if include_unassigned:
            return stmt
        return stmt.where(Device.village_id.is_not(None))
    return stmt.where(Device.village_id.in_(scope.village_ids))


# ── 조회 ─────────────────────────────────────────────────────────────────
async def list_devices(
    db: AsyncSession,
    scope: VillageScope,
    *,
    village_id: int | None = None,
    zone_id: int | None = None,
    status_filter: str | None = None,
    q: str | None = None,
) -> list[DeviceOut]:
    if village_id is not None:
        scope.ensure_allowed(village_id)

    # 상태 필터가 unassigned 일 때만 미배정을 포함한다.
    want_unassigned = status_filter == "unassigned"
    stmt = _scoped(_base_query(), scope, include_unassigned=want_unassigned)

    if want_unassigned:
        stmt = stmt.where(Device.village_id.is_(None))
    if village_id is not None:
        stmt = stmt.where(Device.village_id == village_id)
    if zone_id is not None:
        stmt = stmt.where(Device.zone_id == zone_id)

    cutoff = online_cutoff()
    if status_filter == "online":
        stmt = stmt.where(online_clause(cutoff))
    elif status_filter == "offline":
        stmt = stmt.where(not_(online_clause(cutoff)))

    if q:
        needle = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(func.lower(Device.mac).like(needle), func.lower(Device.label).like(needle))
        )

    rows = (await db.execute(stmt.order_by(Device.mac))).all()
    return [
        DeviceOut.from_row(
            device, online=is_online(device, cutoff),
            village_name=village_name, zone_name=zone_name,
        )
        for device, village_name, zone_name in rows
    ]


async def list_unassigned(db: AsyncSession) -> list[DeviceOut]:
    """미배정 단말. super_admin 전용 — 라우터가 SuperAdmin 가드를 건다."""
    cutoff = online_cutoff()
    rows = (
        await db.execute(_base_query().where(Device.village_id.is_(None)).order_by(Device.mac))
    ).all()
    return [
        DeviceOut.from_row(d, online=is_online(d, cutoff))
        for d, _, _ in rows
    ]


async def get_device(db: AsyncSession, mac: str, scope: VillageScope) -> DeviceDetail:
    row = (await db.execute(_base_query().where(Device.mac == mac))).first()
    if row is None:
        raise DeviceNotFound()
    device, village_name, zone_name = row
    scope.ensure_allowed(device.village_id)

    base = DeviceOut.from_row(
        device,
        online=is_online(device, online_cutoff()),
        village_name=village_name,
        zone_name=zone_name,
    )
    return DeviceDetail(**base.model_dump(), last_status=device.last_status)


async def count_by_status(db: AsyncSession, scope: VillageScope) -> dict[str, int]:
    """대시보드 요약 타일. 한 번의 질의로 온라인/오프라인/미배정을 센다."""
    cutoff = online_cutoff()
    online_expr = func.count().filter(online_clause(cutoff))
    stmt = select(func.count(), online_expr)

    if scope.all_villages:
        assigned = (await db.execute(stmt.where(Device.village_id.is_not(None)))).one()
        unassigned = await db.scalar(
            select(func.count()).select_from(Device).where(Device.village_id.is_(None))
        )
    else:
        assigned = (
            await db.execute(stmt.where(Device.village_id.in_(scope.village_ids)))
        ).one()
        unassigned = 0  # village_admin 에게는 미배정 개념을 노출하지 않는다

    total, online = int(assigned[0]), int(assigned[1] or 0)
    return {
        "total": total,
        "online": online,
        "offline": total - online,
        "unassigned": int(unassigned or 0),
    }


# ── 대상 해석 ────────────────────────────────────────────────────────────
async def macs_for_target(
    db: AsyncSession,
    *,
    target_scope: str,
    target_id: str | None,
    scope: VillageScope,
    online_only: bool = True,
) -> list[str]:
    """방송 대상 → MAC 목록.

    zone / device 는 MAC 단위로 펼쳐서 발행해야 하므로 여기서 해석한다.
    online_only=True 면 최근 5분 내 통신한 단말만 센다 — 통신 사양상 대상 카운트는
    온라인 단말만 넣는다.
    """
    stmt = select(Device.mac)

    if target_scope == "device":
        if not target_id:
            raise ApiError("대상 단말 MAC 이 필요합니다.")
        stmt = stmt.where(Device.mac == target_id)
    elif target_scope == "zone":
        if not target_id:
            raise ApiError("대상 구역 id 가 필요합니다.")
        zone_id = int(target_id)
        scope.ensure_allowed(await org_service.zone_village_id(db, zone_id))
        stmt = stmt.where(Device.zone_id == zone_id)
    elif target_scope == "village":
        if not target_id:
            raise ApiError("대상 마을 id 가 필요합니다.")
        village_id = int(target_id)
        scope.ensure_allowed(village_id)
        stmt = stmt.where(Device.village_id == village_id)
    else:  # all
        stmt = stmt.where(Device.village_id.is_not(None))

    stmt = _scoped(stmt, scope, include_unassigned=False)
    if online_only:
        stmt = stmt.where(online_clause(online_cutoff()))

    return list((await db.scalars(stmt)).all())


# ── 변경 ─────────────────────────────────────────────────────────────────
async def create_device(db: AsyncSession, payload: DeviceCreate, scope: VillageScope) -> DeviceOut:
    if await db.get(Device, payload.mac) is not None:
        raise DeviceAlreadyExists(detail={"mac": payload.mac})
    await _validate_assignment(db, payload.village_id, payload.zone_id, scope)

    device = Device(**payload.model_dump())
    db.add(device)
    await db.flush()
    return DeviceOut.from_row(device, online=False)


async def _validate_assignment(
    db: AsyncSession, village_id: int | None, zone_id: int | None, scope: VillageScope
) -> None:
    """배정 정합성.

    구역은 반드시 그 단말의 마을에 속해야 한다. DB 제약으로 걸지 않고 여기서 본다
    (스키마 문서의 '지금 일부러 뺀 것' 참고).
    """
    if village_id is not None:
        scope.ensure_allowed(village_id)
        if not await org_service.village_exists(db, village_id):
            raise VillageNotFound(detail={"village_id": village_id})

    if zone_id is not None:
        if village_id is None:
            raise ApiError(
                "구역만 배정할 수 없습니다. 마을을 먼저 지정하세요.",
                code="ZONE_WITHOUT_VILLAGE",
            )
        owner = await org_service.zone_village_id(db, zone_id)
        if owner != village_id:
            raise ApiError(
                "구역이 해당 마을 소속이 아닙니다.",
                code="ZONE_VILLAGE_MISMATCH",
                detail={"zone_id": zone_id, "zone_village_id": owner},
            )


async def update_device(
    db: AsyncSession,
    mac: str,
    payload: DeviceUpdate,
    scope: VillageScope,
    publisher: MqttPublisher,
    *,
    config_version: int,
) -> DeviceDetail:
    """단말 수정. 마을 배정이 바뀌면 CONFIG 를 다시 내려보낸다.

    보낸 필드만 반영한다(exclude_unset) — null 을 명시하면 해제, 생략하면 미변경이다.
    """
    device = await db.get(Device, mac)
    if device is None:
        raise DeviceNotFound()
    scope.ensure_allowed(device.village_id)

    data = payload.model_dump(exclude_unset=True)
    new_village = data.get("village_id", device.village_id)
    new_zone = data.get("zone_id", device.zone_id)

    # 마을이 바뀌는데 구역을 같이 안 보냈으면 구역은 자동 해제한다(다른 마을 구역이 남으면 안 된다).
    if "village_id" in data and new_village != device.village_id and "zone_id" not in data:
        new_zone = None

    await _validate_assignment(db, new_village, new_zone, scope)

    village_changed = new_village != device.village_id
    if "label" in data:
        device.label = data["label"]
    device.village_id = new_village
    device.zone_id = new_zone
    await db.flush()

    if village_changed:
        # 실패해도 단말 수정 자체는 유지한다. 다음 CONFIG 재조정 주기가 따라잡는다.
        try:
            await publisher.publish_device_config(
                mac=mac, village_id=new_village, config_version=config_version
            )
        except Exception:  # noqa: BLE001
            log.exception("배정 후 CONFIG 발행 실패 (재조정 주기가 복구): %s", mac)
        await _republish_global_config(db, publisher)

    return await get_device(db, mac, scope)


async def _republish_global_config(db: AsyncSession, publisher: MqttPublisher) -> None:
    """마을 배정이 바뀌었으니 공통 CONFIG 의 village_id 를 다시 내린다.

    현재 펌웨어는 `iotradio/all/config` 만 구독하므로, 위의 단말별 발행만으로는
    단말이 마을을 영영 모른다(통신 사양 §3.5).

    config_version 을 같이 올리는 게 핵심이다 — 같은 토픽에 retain 으로 덮어쓰는
    구조라, 버전이 그대로면 단말이 "이미 적용한 설정"으로 보고 무시한다.
    """
    config = await db.get(CurrentConfig, 1)
    if config is None:
        return
    config.config_version += 1
    await db.flush()
    try:
        await publisher.publish_global_config(
            config_version=config.config_version,
            status_interval_sec=config.status_interval_sec,
            live_stats_interval_sec=config.live_stats_interval_sec,
            event_qos=config.event_qos,
            village_id=await config_reconcile.shared_village_id(db),
        )
    except Exception:  # noqa: BLE001
        log.exception("공통 CONFIG 재발행 실패 (재조정 주기가 복구)")


async def delete_device(
    db: AsyncSession, mac: str, scope: VillageScope, publisher: MqttPublisher
) -> None:
    device = await db.get(Device, mac)
    if device is None:
        raise DeviceNotFound()
    scope.ensure_allowed(device.village_id)

    await db.delete(device)
    await db.flush()
    await clear_device_configs(publisher, [mac], db)


async def clear_device_configs(
    publisher: MqttPublisher, macs: Sequence[str], db: AsyncSession | None = None
) -> None:
    """단말별 CONFIG retain 을 지운다.

    안 지우면 단말이 재접속할 때 브로커가 없어진 배정을 다시 물려준다.
    마을 삭제 · 단말 삭제 · 배정 해제 후에 부른다.

    db 를 넘기면 공통 CONFIG 의 village_id 도 다시 맞춘다 — 마지막 단말이
    빠져서 배정이 없어졌는데 공통 CONFIG 에 옛 마을이 남아 있으면, 새로 붙는
    단말이 없어진 마을로 배정된다.
    """
    for mac in macs:
        try:
            await publisher.publish_device_config(mac=mac, village_id=None, config_version=0)
        except Exception:  # noqa: BLE001
            log.exception("CONFIG retain 삭제 실패: %s", mac)

    if db is not None:
        await _republish_global_config(db, publisher)
