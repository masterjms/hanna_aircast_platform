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
from sqlalchemy import Select, not_, select
from sqlalchemy.orm import aliased

from app.config import settings
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
    #: 대상의 사람이 읽는 이름. 화면은 내부 id 대신 이걸 쓴다(문제점 33번).
    target_label: str = ""
    triggered_at: dt.datetime


class RecentEvent(BaseModel):
    id: int
    event_type: str
    target_scope: str
    target_ids: list[str]
    target_label: str = ""
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
    village_id: int | None
    village_name: str | None
    #: 라이브 수신 상태 — OFF/PLAYING/RECONNECTING. RECONNECTING = 방송 중 무음.
    live: str | None
    #: normal | offline | unassigned. 방송중 상태는 Phase 3 에서 붙는다.
    marker: str
    #: 좌표 출처 — device(자체 입력) | zone | village(fallback).
    #: fallback 마커는 "이 근처 어딘가"라는 뜻이라 화면이 구분해 그린다.
    position_source: str


class MapVillage(BaseModel):
    """지도 위 마을 라벨·경계용."""

    id: int
    name: str
    b_code: str | None
    lat: float | None
    lng: float | None
    #: 경계 폴리곤(GeoJSON geometry, WGS84). 안 넣은 마을은 null 이라 화면이 건너뛴다.
    #: 「구역의 도형」을 b_code 로 조인해 넣는다 — scripts/import_boundaries.py.
    boundary: dict[str, Any] | None = None


class MapOut(BaseModel):
    #: 지도 SDK 로드용 JavaScript 키. 공개 키(도메인 등록으로 보호)라 응답에 실어도 된다.
    #: 미설정이면 null — 화면이 "키 설정 필요" 안내를 띄운다.
    kakao_js_key: str | None
    villages: list[MapVillage]
    pins: list[MapPin]
    #: 좌표가 전혀 없어(자체·구역·마을 모두) 지도에 못 찍는 단말.
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


async def _visible_events(
    db, stmt: Select, scope: VillageScope, *, limit: int | None = None
) -> list[BroadcastEvent]:
    """이력을 범위로 거른다 — 대상 단말의 마을이 내 범위에 걸리는 행(설계 §8).

    broadcast_events 에는 village_id 컬럼이 없고 target_scope/target_ids 로만 대상이
    남아서 SQL 로 바로 거르기 어렵다. 예전에는 village 대상만 JSONB contains 로
    걸렀고 device·zone·all 은 빠졌다. 지금은 후보를 넉넉히 읽어 같은 판정으로 거른다.
    """
    if scope.all_villages:
        rows = list((await db.scalars(stmt.limit(limit) if limit else stmt)).all())
        return rows
    if scope.is_empty:
        return []
    # 범위 밖 행이 섞여 있으니 원하는 수의 몇 배를 읽고 거른다. 이력 화면은 최근
    # 10건이라 50건이면 충분하고, 진행 중 방송은 애초에 몇 건 안 된다.
    candidate = stmt.limit(limit * 5) if limit else stmt
    rows = list((await db.scalars(candidate)).all())
    flags = await device_service.events_visible_to(db, rows, scope)
    visible = [e for e, ok in zip(rows, flags, strict=True) if ok]
    return visible[:limit] if limit else visible


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

    active_rows = await _visible_events(
        db,
        select(BroadcastEvent)
        .where(BroadcastEvent.ended_at.is_(None))
        .order_by(BroadcastEvent.triggered_at.desc()),
        scope,
    )
    active = [
        ActiveBroadcast.model_validate(e, from_attributes=True).model_copy(
            update={"target_label": label}
        )
        for e, label in zip(
            active_rows,
            await device_service.describe_targets(
                db, [(e.target_scope, e.target_ids) for e in active_rows]
            ),
            strict=True,
        )
    ]

    recent_rows = await _visible_events(
        db,
        select(BroadcastEvent).order_by(BroadcastEvent.triggered_at.desc()),
        scope,
        limit=_RECENT_EVENT_LIMIT,
    )
    recent = [
        RecentEvent.model_validate(e, from_attributes=True).model_copy(
            update={"target_label": label}
        )
        for e, label in zip(
            recent_rows,
            await device_service.describe_targets(
                db, [(e.target_scope, e.target_ids) for e in recent_rows]
            ),
            strict=True,
        )
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
    """단말 지도 + 목록 패널 데이터.

    좌표 fallback 은 자체 입력 → 구역 → 마을 순이다(지도 설계 §2.2 — 마을 값을
    복사하지 않고 조회 시점에 대신 쓴다). 전부 없으면 missing_location.
    """
    village = aliased(Village)
    zone = aliased(Zone)
    stmt = _scoped_devices(
        select(
            Device.mac,
            Device.label,
            Device.last_seen_at,
            Device.last_status,
            Device.village_id,
            village.name,
            Device.lat,
            Device.lng,
            zone.lat,
            zone.lng,
            village.lat,
            village.lng,
        )
        .outerjoin(village, Device.village_id == village.id)
        .outerjoin(zone, Device.zone_id == zone.id),
        scope,
    )

    cutoff = online_cutoff()
    pins: list[MapPin] = []
    missing: list[str] = []

    for (
        mac, label, last_seen, last_status, village_id, village_name,
        d_lat, d_lng, z_lat, z_lng, v_lat, v_lng,
    ) in (await db.execute(stmt)).all():
        if d_lat is not None and d_lng is not None:
            lat, lng, source = d_lat, d_lng, "device"
        elif z_lat is not None and z_lng is not None:
            lat, lng, source = z_lat, z_lng, "zone"
        elif v_lat is not None and v_lng is not None:
            lat, lng, source = v_lat, v_lng, "village"
        else:
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
                village_id=village_id,
                village_name=village_name,
                live=(last_status or {}).get("live"),
                marker=marker,
                position_source=source,
            )
        )

    village_stmt = scope.apply(select(Village).order_by(Village.name), Village.id)
    villages = [
        MapVillage(
            id=v.id, name=v.name, b_code=v.b_code, lat=v.lat, lng=v.lng, boundary=v.boundary
        )
        for v in (await db.scalars(village_stmt)).all()
    ]

    return MapOut(
        kakao_js_key=settings.kakao_js_key,
        villages=villages,
        pins=pins,
        missing_location=missing,
    )
