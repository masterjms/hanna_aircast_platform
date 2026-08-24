"""대시보드 — 읽기 전용 집계(read model).

이 모듈만은 여러 도메인의 테이블을 가로질러 읽는다. 화면 하나에 단말·마을·이력이
같이 나와야 하는데 이를 모듈별 서비스 호출로 쪼개면 N+1 질의가 되기 때문이다.
대신 여기서는 절대 쓰기를 하지 않는다.

갱신은 폴링이다 — 기본 5초, 방송 진행 중에는 프론트가 2초로 당긴다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import Select, false, func, not_, or_, select
from sqlalchemy.orm import aliased

from app.core.deps import Db, Scope
from app.core.presence import is_online, online_clause, online_cutoff
from app.core.scope import VillageScope
from app.models.device import Device
from app.models.event import BroadcastEvent
from app.models.org import Village, Zone
from app.modules.device import service as device_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

#: 대시보드 '이상 상태' 목록에 최대 몇 대까지 보여줄지.
_ALERT_LIMIT = 20
_RECENT_EVENT_LIMIT = 10


class DeviceCounts(BaseModel):
    total: int
    online: int
    offline: int
    unassigned: int


class AlertItem(BaseModel):
    mac: str
    label: str | None
    village_name: str | None
    reason: str
    last_seen_at: dt.datetime | None


class ActiveBroadcast(BaseModel):
    id: int
    job_id: int | None
    event_type: str
    target_scope: str
    target_ids: list[str]
    triggered_at: dt.datetime


class RecentEvent(BaseModel):
    id: int
    event_type: str
    target_scope: str
    target_ids: list[str]
    triggered_at: dt.datetime
    ended_at: dt.datetime | None


class SummaryOut(BaseModel):
    scope: dict[str, Any]
    devices: DeviceCounts
    alerts: list[AlertItem]
    active_broadcasts: list[ActiveBroadcast]
    recent_events: list[RecentEvent]


class MapPin(BaseModel):
    mac: str
    label: str | None
    lat: float
    lng: float
    online: bool
    village_name: str | None
    #: normal | offline | unassigned. 방송중 상태는 Phase 3 에서 붙는다.
    marker: str


class MapOut(BaseModel):
    pins: list[MapPin]
    #: 좌표가 없어 지도에 못 찍는 단말. 화면 설계상 별도 목록으로 보여준다.
    missing_location: list[str]


def _alert_reason(device: Device) -> str:
    if device.last_seen_at is None:
        return "한 번도 통신하지 않음"
    if (device.last_status or {}).get("state") == "OFFLINE":
        return "연결 끊김(LWT)"
    return "응답 없음"


def _scoped_devices(stmt: Select, scope: VillageScope) -> Select:
    if scope.all_villages:
        return stmt
    return stmt.where(Device.village_id.in_(scope.village_ids))


def _scoped_events(stmt: Select, scope: VillageScope) -> Select:
    """이력의 마을 범위 필터.

    broadcast_events 에는 village_id 컬럼이 없고 target_scope/target_id 로만 대상이 남는다.
    그래서 village_admin 에게는 '내 마을을 대상으로 한 행'만 보여준다.
    전체(all) 방송은 담당 마을에도 나갔지만 대상 표기가 'all' 이라 여기서는 제외된다.

    ⚠ Phase 3 에서 방송 이력이 실제로 쌓이기 시작하면, 이 필터는 device_events 조인으로
      "내 마을 단말이 실제로 받은 방송"을 기준으로 바꾸는 게 정확하다.
    """
    if scope.all_villages:
        return stmt
    if scope.is_empty:
        # 담당 마을이 없으면 볼 수 있는 이력도 없다.
        return stmt.where(false())
    # target_ids 는 JSONB 목록이다. "내 마을 중 하나라도 대상에 들어 있는 행"을
    # @>(contains) 를 마을별로 OR 해서 찾는다 — 이력 테이블 규모에서는 충분하다.
    return stmt.where(
        BroadcastEvent.target_scope == "village",
        or_(*[BroadcastEvent.target_ids.contains([str(v)]) for v in sorted(scope.village_ids)]),
    )


@router.get("/summary", response_model=SummaryOut)
async def summary(db: Db, scope: Scope) -> SummaryOut:
    counts = await device_service.count_by_status(db, scope)
    cutoff = online_cutoff()

    # 이상 상태 = 오프라인 단말. 판정은 device 모듈 것을 그대로 쓴다
    # (여기서 따로 만들면 요약 타일과 이 목록이 어긋난다).
    village = aliased(Village)
    alert_stmt = _scoped_devices(
        select(Device, village.name)
        .outerjoin(village, Device.village_id == village.id)
        .where(not_(online_clause(cutoff)))
        .order_by(Device.last_seen_at.asc().nulls_first())
        .limit(_ALERT_LIMIT),
        scope,
    )
    alerts = [
        AlertItem(
            mac=d.mac,
            label=d.label,
            village_name=vname,
            reason=_alert_reason(d),
            last_seen_at=d.last_seen_at,
        )
        for d, vname in (await db.execute(alert_stmt)).all()
    ]

    active_stmt = _scoped_events(
        select(BroadcastEvent)
        .where(BroadcastEvent.ended_at.is_(None))
        .order_by(BroadcastEvent.triggered_at.desc()),
        scope,
    )
    active = [
        ActiveBroadcast.model_validate(e, from_attributes=True)
        for e in (await db.scalars(active_stmt)).all()
    ]

    recent_stmt = _scoped_events(
        select(BroadcastEvent)
        .order_by(BroadcastEvent.triggered_at.desc())
        .limit(_RECENT_EVENT_LIMIT),
        scope,
    )
    recent = [
        RecentEvent.model_validate(e, from_attributes=True)
        for e in (await db.scalars(recent_stmt)).all()
    ]

    return SummaryOut(
        scope=scope.to_dict(),
        devices=DeviceCounts(**counts),
        alerts=alerts,
        active_broadcasts=active,
        recent_events=recent,
    )


@router.get("/map", response_model=MapOut)
async def device_map(db: Db, scope: Scope) -> MapOut:
    """단말 지도.

    devices 테이블에는 좌표가 없다. 구역 좌표를 우선 쓰고, 없으면 마을 좌표로 떨어진다.
    둘 다 없으면 지도에 못 찍으므로 missing_location 으로 따로 넘긴다.
    """
    village = aliased(Village)
    zone = aliased(Zone)
    stmt = _scoped_devices(
        select(
            Device.mac,
            Device.label,
            Device.last_seen_at,
            Device.last_status,
            village.name,
            func.coalesce(zone.lat, village.lat),
            func.coalesce(zone.lng, village.lng),
        )
        .outerjoin(village, Device.village_id == village.id)
        .outerjoin(zone, Device.zone_id == zone.id),
        scope,
    )

    cutoff = online_cutoff()
    pins: list[MapPin] = []
    missing: list[str] = []

    for mac, label, last_seen, last_status, village_name, lat, lng in (
        await db.execute(stmt)
    ).all():
        if lat is None or lng is None:
            missing.append(mac)
            continue
        # 목록·타일과 같은 규칙을 쓰기 위해 임시 Device 로 감싼다.
        online = is_online(
            Device(mac=mac, last_seen_at=last_seen, last_status=last_status), cutoff
        )
        if village_name is None:
            marker = "unassigned"
        elif online:
            marker = "normal"
        else:
            marker = "offline"
        pins.append(
            MapPin(
                mac=mac,
                label=label,
                lat=float(lat),
                lng=float(lng),
                online=online,
                village_name=village_name,
                marker=marker,
            )
        )

    return MapOut(pins=pins, missing_location=missing)
