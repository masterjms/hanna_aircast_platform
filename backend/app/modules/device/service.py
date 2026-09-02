"""단말 서비스.

이 모듈이 devices 테이블을 소유한다. 대상 해석(구역 → MAC 목록)도 여기 책임이다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import Select, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.core import mqtt_accounts
from app.core.presence import is_online, online_clause, online_cutoff
from app.core.scope import VillageScope
from app.errors import ApiError, DeviceAlreadyExists, DeviceNotFound, VillageNotFound
from app.models.device import Device
from app.models.org import Village, Zone
from app.models.system import CurrentConfig
from app.modules.org import service as org_service
from app.mqtt.publisher import MqttPublisher
from app.schemas.device import (
    DeviceCreate,
    DeviceCredentialOut,
    DeviceDetail,
    DeviceOut,
    DeviceUpdate,
)
from app.tasks import config_reconcile

log = logging.getLogger(__name__)





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
    target_ids: Sequence[str],
    scope: VillageScope,
    online_only: bool = True,
) -> list[str]:
    """방송 대상 → MAC 목록.

    대상은 목록이다 — "마을 2곳 동시 방송"을 값 하나로는 표현할 수 없다.
    village_admin 은 목록 **전부**가 담당 범위여야 한다. 하나라도 남의 마을이면
    전체를 거절한다 — 일부만 나가는 방송은 운영자가 의도한 것이 아니다.

    online_only=True 면 최근 5분 내 통신한 단말만 센다 — 통신 사양상 대상 카운트는
    온라인 단말만 넣는다.
    """
    stmt = select(Device.mac)

    if target_scope == "device":
        if not target_ids:
            raise ApiError("대상 단말 MAC 이 필요합니다.")
        stmt = stmt.where(Device.mac.in_(list(target_ids)))
    elif target_scope == "zone":
        if not target_ids:
            raise ApiError("대상 구역 id 가 필요합니다.")
        zone_ids = [int(z) for z in target_ids]
        for zone_id in zone_ids:
            scope.ensure_allowed(await org_service.zone_village_id(db, zone_id))
        stmt = stmt.where(Device.zone_id.in_(zone_ids))
    elif target_scope == "village":
        if not target_ids:
            raise ApiError("대상 마을 id 가 필요합니다.")
        village_ids = [int(v) for v in target_ids]
        for village_id in village_ids:
            scope.ensure_allowed(village_id)
        stmt = stmt.where(Device.village_id.in_(village_ids))
    else:  # all
        stmt = stmt.where(Device.village_id.is_not(None))

    stmt = _scoped(stmt, scope, include_unassigned=False)
    if online_only:
        stmt = stmt.where(online_clause(online_cutoff()))

    # 단말별 계정만 쓰는 단계(공유 계정 제거 후)에서는 계정 미발행 단말을 대상에서
    # 뺀다 — 「미등록*」(동기화 어긋남) 단말이다. 레지스트리 사양 §3.6:
    # "어느 쪽이든 그 전까지는 방송 대상 목록에 넣지 않는다".
    # 이행기(공유 계정이 살아 있는 동안)에는 걸지 않는다 — 현장 단말이 아직
    # 공유 계정으로 붙어 있어서, 걸면 정상 단말이 방송에서 빠진다.
    if _device_accounts_enforced():
        stmt = stmt.where(Device.mqtt_password.is_not(None))

    return list((await db.scalars(stmt)).all())


def _device_accounts_enforced() -> bool:
    """단말별 계정이 유일한 접속 경로인가.

    passwd 내보내기가 켜져 있고(운영) 공유 계정이 제거된 뒤에만 True.
    개발·테스트는 anonymous 브로커라 항상 False.
    """
    return bool(settings.mosquitto_passwd_export) and not settings.mqtt_device_password


# ── 단말별 MQTT 계정 ─────────────────────────────────────────────────────
async def export_broker_accounts(db: AsyncSession) -> None:
    """DB 의 계정·마을 배정을 mosquitto passwd + aclfile 로 내보낸다.

    등록/삭제/마을 배정 변경/마을 삭제/기동 때 호출. ACL 은 단말마다 자기 마을
    topic 만 여는 파일이라(통신 사양 §2.1 "별도 규칙") 배정이 바뀌면 같이 다시
    만들어야 한다 — 안 하면 옛 마을 명령을 계속 듣거나 새 마을 명령이 안 온다.

    실패해도 예외를 던지지 않는다 — 정본은 DB 이고 다음 호출이 따라잡는다.
    """
    rows = (
        await db.execute(
            select(Device.mac, Device.mqtt_password, Device.village_id).where(
                Device.mqtt_password.is_not(None)
            )
        )
    ).all()
    mqtt_accounts.export_passwd({mac: pw for mac, pw, _ in rows})
    mqtt_accounts.export_acl({mac: village_id for mac, _, village_id in rows})


async def issue_credential(
    db: AsyncSession, mac: str, *, reissue: bool = False
) -> DeviceCredentialOut:
    """계정 발행/조회. 이미 있으면 재사용이 기본이다(계정 사양 §4 — 계정 불변).

    reissue=True 는 라인 재작업 전용 — 케이블이 꽂힌 단말에 그 자리에서 새 값을
    넣을 때만 쓴다. 현장에 나가 있는 단말의 계정을 바꾸는 기능이 아니다.
    """
    device = await db.get(Device, mac)
    if device is None:
        raise DeviceNotFound()

    issued = False
    if device.mqtt_password is None or reissue:
        device.mqtt_password = mqtt_accounts.generate_device_password()
        await db.flush()
        issued = True
        # DB 와 브로커는 한 묶음이다(레지스트리 사양 §3.6). flush 직후 바로 내보낸다.
        await export_broker_accounts(db)

    return DeviceCredentialOut(
        username=device.mac,
        password=device.mqtt_password,
        server_host=mqtt_accounts.server_host(),
        issued=issued,
    )


# ── 변경 ─────────────────────────────────────────────────────────────────
async def create_device(db: AsyncSession, payload: DeviceCreate, scope: VillageScope) -> DeviceOut:
    if await db.get(Device, payload.mac) is not None:
        raise DeviceAlreadyExists(detail={"mac": payload.mac})
    await _validate_assignment(db, payload.village_id, payload.zone_id, scope)

    # 등록 = DB 기록 + 브로커 계정 발행, 한 묶음이다(레지스트리 사양 §3.6).
    # 등록 화면이 모달을 열며 미리 발급받은 비밀번호(이미 시리얼로 단말에 넣었을 수
    # 있는 값)를 보내오면 그대로 쓰고, 없으면 여기서 생성한다.
    data = payload.model_dump()
    if not data.get("mqtt_password"):
        data["mqtt_password"] = mqtt_accounts.generate_device_password()
    device = Device(**data)
    db.add(device)
    await db.flush()
    await export_broker_accounts(db)
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
    # 설치 위치 — 보낸 필드만 반영(생략=미변경, null=지우기). 지우면 지도는
    # 마을 좌표 fallback 으로 돌아간다.
    for field in ("road_address", "jibun_address", "address_detail", "lat", "lng"):
        if field in data:
            setattr(device, field, data[field])
    device.village_id = new_village
    device.zone_id = new_zone
    await db.flush()

    if village_changed:
        if new_village is None:
            # 해제는 그 단말의 보관본을 명시적으로 지워야 한다. resync 는 배정된
            # 단말만 발행하므로, 안 지우면 옛 배정이 브로커에 남아서 단말이
            # 재접속할 때 되살아난다.
            await clear_device_configs(publisher, [mac])
        await resync_config(db, publisher)
        # ACL 도 마을을 따라간다 — 이 단말이 읽을 수 있는 village topic 이 바뀐다.
        await export_broker_accounts(db)

    return await get_device(db, mac, scope)


async def resync_config(db: AsyncSession, publisher: MqttPublisher) -> None:
    """config_version 을 올리고 CONFIG 두 토픽을 함께 다시 내린다.

    버전을 올리는 이유: 같은 토픽에 retain 으로 덮어쓰는 구조라, 버전이 그대로면
    단말이 "이미 적용한 설정"으로 보고 무시한다.

    두 토픽을 함께 보내는 이유: 단말은 토픽별로 버전을 따로 추적하지 않고 마지막에
    받은 값을 그대로 쓰는 단일 카운터 구조다(사양 §4.3). 한쪽만 올리면 단말이
    낡은 값을 최종본으로 보고하게 된다.

    발행이 실패해도 DB 변경은 유지한다 — 정본은 DB 이고, 재조정 주기가 따라잡는다.
    """
    config = await db.get(CurrentConfig, 1)
    if config is None:
        return
    config.config_version += 1
    await db.flush()
    try:
        await config_reconcile.publish_all(publisher, db)
    except Exception:  # noqa: BLE001
        log.exception("CONFIG 재발행 실패 (재조정 주기가 복구)")


async def delete_device(
    db: AsyncSession, mac: str, scope: VillageScope, publisher: MqttPublisher
) -> None:
    device = await db.get(Device, mac)
    if device is None:
        raise DeviceNotFound()
    scope.ensure_allowed(device.village_id)

    await db.delete(device)
    await db.flush()
    # 삭제 = DB 제거 + 브로커 계정 제거, 한 묶음(레지스트리 사양 §3.6).
    # 도난·회수 실패 단말을 막는 유일한 수단이 이 계정 삭제다(계정 사양 §4.1).
    await export_broker_accounts(db)
    await clear_device_configs(publisher, [mac], db)


async def clear_device_configs(
    publisher: MqttPublisher, macs: Sequence[str], db: AsyncSession | None = None
) -> None:
    """단말별 CONFIG retain 을 지운다.

    안 지우면 단말이 재접속할 때 브로커가 없어진 배정을 다시 물려준다.
    마을 삭제 · 단말 삭제 · 배정 해제 후에 부른다.

    db 를 넘기면 config_version 을 올려 두 토픽을 다시 맞춘다 — 안 그러면 남은
    단말들이 낡은 버전을 최종본으로 들고 있게 된다.
    """
    for mac in macs:
        try:
            await publisher.publish_device_config(mac=mac, village_id=None, config_version=0)
        except Exception:  # noqa: BLE001
            log.exception("CONFIG retain 삭제 실패: %s", mac)

    if db is not None:
        await resync_config(db, publisher)
