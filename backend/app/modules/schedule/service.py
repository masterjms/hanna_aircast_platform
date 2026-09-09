"""자동방송 스케줄 서비스.

schedules / schedule_runs 테이블을 소유한다. 실행 시각 계산은 rules.py, 실제 실행은
app/tasks/schedule_runner.py 가 한다.

권한(스케줄 설계 2026-09-09):
  보기       대상이 내 범위와 하나라도 겹치면 — 진행 중 방송 가시성과 같은 판정
  수정·삭제  대상 **전체**가 내 범위 안일 때만. 군청이 건 관할 전체 스케줄을 이장이 지우면
             안 된다(방송 중지는 "보이면 멈춘다"가 맞지만 그건 긴급 제어다)
  만들기     대상이 내 범위 안. organization 은 내 관할 기관이어야
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import SCHEDULE_MAX, ScheduleTarget
from app.core.scope import VillageScope
from app.errors import (
    ApiError,
    DeviceNotFound,
    NotFound,
    OrganizationNotFound,
    OrganizationOutOfScope,
    VillageNotFound,
)
from app.models.device import Device
from app.models.file import File
from app.models.org import Organization, Village
from app.models.schedule import Schedule, ScheduleRun
from app.modules.device import service as device_service
from app.modules.schedule import rules
from app.schemas.schedule import (
    OccurrenceOut,
    ScheduleCreate,
    ScheduleOut,
    ScheduleRunOut,
    ScheduleUpdate,
)

log = logging.getLogger(__name__)


class ScheduleNotFound(NotFound):
    code = "SCHEDULE_NOT_FOUND"
    message = "존재하지 않는 스케줄입니다."


# ── 대상 해석 ────────────────────────────────────────────────────────────
async def expand_org_villages(db: AsyncSession, org_ids: Sequence[int]) -> list[int]:
    """기관(자기 + 바로 아래 기관) 소속 마을 id.

    실행 시점에 부르므로 새로 영입한 마을도 자동으로 들어간다.
    """
    if not org_ids:
        return []
    children = select(Organization.id).where(Organization.parent_id.in_(org_ids))
    rows = await db.scalars(
        select(Village.id).where(
            or_(Village.organization_id.in_(org_ids), Village.organization_id.in_(children))
        )
    )
    return sorted(set(rows.all()))


def _ints(values: Sequence[str]) -> list[int]:
    out = []
    for v in values:
        try:
            out.append(int(v))
        except ValueError:
            continue
    return out


async def target_villages(
    db: AsyncSession, target_scope: str, target_ids: Sequence[str]
) -> set[int]:
    """스케줄 대상이 닿는 마을 집합. 가시성·수정 권한 판정에 쓴다."""
    if target_scope == ScheduleTarget.VILLAGE.value:
        return set(_ints(target_ids))
    if target_scope == ScheduleTarget.ORGANIZATION.value:
        return set(await expand_org_villages(db, _ints(target_ids)))
    rows = await db.scalars(
        select(Device.village_id).where(
            Device.mac.in_([str(m) for m in target_ids]), Device.village_id.is_not(None)
        )
    )
    return set(rows.all())


def _visible(villages: set[int], scope: VillageScope) -> bool:
    return scope.all_villages or any(scope.allows(v) for v in villages)


def _editable(villages: set[int], scope: VillageScope) -> bool:
    if scope.all_villages:
        return True
    return bool(villages) and all(scope.allows(v) for v in villages)


async def _validate_target(
    db: AsyncSession,
    target_scope: str,
    target_ids: Sequence[str],
    scope: VillageScope,
    org_ids: set[int] | None,
) -> None:
    """대상이 존재하고 내 범위 안인지."""
    if target_scope == ScheduleTarget.VILLAGE.value:
        for raw in target_ids:
            vid = int(raw)
            scope.ensure_allowed(vid)
            if await db.get(Village, vid) is None:
                raise VillageNotFound(detail={"village_id": vid})
    elif target_scope == ScheduleTarget.DEVICE.value:
        for mac in target_ids:
            device = await db.get(Device, str(mac))
            if device is None:
                raise DeviceNotFound(detail={"mac": mac})
            if device.village_id is None:
                raise ApiError(
                    "미배정 단말에는 스케줄을 걸 수 없습니다.", code="DEVICE_UNASSIGNED"
                )
            scope.ensure_allowed(device.village_id)
    else:
        for raw in target_ids:
            oid = int(raw)
            if await db.get(Organization, oid) is None:
                raise OrganizationNotFound(detail={"organization_id": oid})
            if org_ids is not None and oid not in org_ids:
                raise OrganizationOutOfScope(detail={"organization_id": oid})


async def _target_label(db: AsyncSession, target_scope: str, target_ids: Sequence[str]) -> str:
    if target_scope == ScheduleTarget.ORGANIZATION.value:
        ids = _ints(target_ids)
        rows = await db.execute(
            select(Organization.name).where(Organization.id.in_(ids)).order_by(Organization.name)
        )
        names = [n for (n,) in rows.all()]
        return f"{', '.join(names)} 관할 전체" if names else ""
    return await device_service.describe_target(
        db, target_scope=target_scope, target_ids=target_ids
    )


# ── 조회 ─────────────────────────────────────────────────────────────────
async def _last_runs(db: AsyncSession, schedule_ids: Sequence[int]) -> dict[int, ScheduleRun]:
    if not schedule_ids:
        return {}
    rows = (
        await db.scalars(
            select(ScheduleRun)
            .where(ScheduleRun.schedule_id.in_(schedule_ids))
            .order_by(ScheduleRun.schedule_id, ScheduleRun.fire_at.desc())
        )
    ).all()
    out: dict[int, ScheduleRun] = {}
    for r in rows:
        out.setdefault(r.schedule_id, r)
    return out


async def _to_out(
    db: AsyncSession,
    s: Schedule,
    scope: VillageScope,
    *,
    now: dt.datetime,
    file_names: dict[int, str],
    last_runs: dict[int, ScheduleRun],
) -> ScheduleOut:
    out = ScheduleOut.model_validate(s)
    out.file_name = file_names.get(s.file_id)
    out.target_label = await _target_label(db, s.target_scope, s.target_ids)
    out.editable = _editable(await target_villages(db, s.target_scope, s.target_ids), scope)
    if s.enabled:
        out.next_fire_at = rules.next_occurrence(rules.rule_of(s), now)
    run = last_runs.get(s.id)
    out.last_run = ScheduleRunOut.model_validate(run) if run else None
    return out


async def _visible_schedules(
    db: AsyncSession, scope: VillageScope, *, enabled_only: bool = False
) -> list[Schedule]:
    stmt = select(Schedule).order_by(Schedule.fire_time, Schedule.id)
    if enabled_only:
        stmt = stmt.where(Schedule.enabled.is_(True))
    rows = (await db.scalars(stmt)).all()
    if scope.all_villages:
        return list(rows)
    if scope.is_empty:
        return []
    out = []
    for s in rows:
        if _visible(await target_villages(db, s.target_scope, s.target_ids), scope):
            out.append(s)
    return out


async def _file_names(db: AsyncSession, ids: Sequence[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await db.execute(select(File.id, File.filename).where(File.id.in_(set(ids))))
    return dict(rows.all())


async def list_schedules(db: AsyncSession, scope: VillageScope) -> list[ScheduleOut]:
    schedules = await _visible_schedules(db, scope)
    now = dt.datetime.now(dt.timezone.utc)
    names = await _file_names(db, [s.file_id for s in schedules])
    runs = await _last_runs(db, [s.id for s in schedules])
    return [
        await _to_out(db, s, scope, now=now, file_names=names, last_runs=runs)
        for s in schedules
    ]


async def get_schedule(db: AsyncSession, schedule_id: int, scope: VillageScope) -> ScheduleOut:
    s = await _load(db, schedule_id, scope)
    now = dt.datetime.now(dt.timezone.utc)
    names = await _file_names(db, [s.file_id])
    runs = await _last_runs(db, [s.id])
    return await _to_out(db, s, scope, now=now, file_names=names, last_runs=runs)


async def _load(db: AsyncSession, schedule_id: int, scope: VillageScope) -> Schedule:
    s = await db.get(Schedule, schedule_id)
    if s is None:
        raise ScheduleNotFound()
    if not _visible(await target_villages(db, s.target_scope, s.target_ids), scope):
        raise ScheduleNotFound()
    return s


async def _ensure_editable(db: AsyncSession, s: Schedule, scope: VillageScope) -> None:
    if not _editable(await target_villages(db, s.target_scope, s.target_ids), scope):
        raise ApiError(
            "대상 전체가 내 관할인 스케줄만 고치거나 지울 수 있습니다.",
            code="SCHEDULE_NOT_EDITABLE",
        )


# ── 생성·수정·삭제 ────────────────────────────────────────────────────────
async def create_schedule(
    db: AsyncSession,
    payload: ScheduleCreate,
    *,
    actor_id: int,
    scope: VillageScope,
    org_ids: set[int] | None,
) -> ScheduleOut:
    count = await db.scalar(select(func.count()).select_from(Schedule))
    if (count or 0) >= SCHEDULE_MAX:
        raise ApiError(
            f"스케줄은 최대 {SCHEDULE_MAX}개까지 등록할 수 있습니다.", code="SCHEDULE_LIMIT"
        )
    if await db.get(File, payload.file_id) is None:
        raise ApiError("존재하지 않는 파일입니다.", code="FILE_NOT_FOUND")
    await _validate_target(db, payload.target_scope.value, payload.target_ids, scope, org_ids)

    s = Schedule(
        repeat=payload.repeat.value,
        weekdays=payload.weekdays,
        month_days=payload.month_days,
        year_dates=[d.model_dump() for d in payload.year_dates] if payload.year_dates else None,
        fire_time=payload.fire_time,
        file_id=payload.file_id,
        target_scope=payload.target_scope.value,
        target_ids=list(payload.target_ids),
        store_flash=payload.store_flash,
        enabled=payload.enabled,
        created_by=actor_id,
    )
    db.add(s)
    await db.flush()
    log.info("스케줄 등록 #%d %s %s", s.id, s.repeat, s.fire_time)
    return await get_schedule(db, s.id, scope)


async def update_schedule(
    db: AsyncSession,
    schedule_id: int,
    payload: ScheduleUpdate,
    *,
    scope: VillageScope,
    org_ids: set[int] | None,
) -> ScheduleOut:
    s = await _load(db, schedule_id, scope)
    await _ensure_editable(db, s, scope)
    data = payload.model_dump(exclude_unset=True)

    # 켜기·끄기만 바꾸는 경우가 대부분이다. 그때는 모양 검사를 다시 하지 않는다.
    if set(data) <= {"enabled", "store_flash"}:
        for k, v in data.items():
            setattr(s, k, v)
        await db.flush()
        return await get_schedule(db, s.id, scope)

    # 나머지는 바꾼 뒤의 전체 모양을 ScheduleCreate 검증기로 다시 통과시킨다 —
    # 매주로 바꾸면서 요일을 안 주면 영영 안 나가는 규칙이 되는 것을 막는다.
    merged = {
        "repeat": s.repeat,
        "weekdays": s.weekdays,
        "month_days": s.month_days,
        "year_dates": s.year_dates,
        "fire_time": s.fire_time,
        "file_id": s.file_id,
        "target_scope": s.target_scope,
        "target_ids": s.target_ids,
        "store_flash": s.store_flash,
        "enabled": s.enabled,
    }
    clearable = {"weekdays", "month_days", "year_dates"}
    merged.update({k: v for k, v in data.items() if v is not None or k in clearable})
    try:
        checked = ScheduleCreate.model_validate(merged)
    except ValueError as exc:
        raise ApiError(str(exc), code="VALIDATION_FAILED") from exc

    if checked.file_id != s.file_id and await db.get(File, checked.file_id) is None:
        raise ApiError("존재하지 않는 파일입니다.", code="FILE_NOT_FOUND")
    target_changed = checked.target_scope.value != s.target_scope or list(
        checked.target_ids
    ) != list(s.target_ids)
    if target_changed:
        await _validate_target(db, checked.target_scope.value, checked.target_ids, scope, org_ids)
        # 새 대상도 전부 내 범위여야 한다 — 남의 마을로 옮겨 놓고 손을 떼는 일을 막는다.
        if not _editable(
            await target_villages(db, checked.target_scope.value, checked.target_ids), scope
        ):
            raise ApiError(
                "대상 전체가 내 관할이어야 합니다.", code="SCHEDULE_NOT_EDITABLE"
            )

    s.repeat = checked.repeat.value
    s.weekdays = checked.weekdays
    s.month_days = checked.month_days
    s.year_dates = [d.model_dump() for d in checked.year_dates] if checked.year_dates else None
    s.fire_time = checked.fire_time
    s.file_id = checked.file_id
    s.target_scope = checked.target_scope.value
    s.target_ids = list(checked.target_ids)
    s.store_flash = checked.store_flash
    s.enabled = checked.enabled
    await db.flush()
    return await get_schedule(db, s.id, scope)


async def delete_schedule(db: AsyncSession, schedule_id: int, *, scope: VillageScope) -> None:
    s = await _load(db, schedule_id, scope)
    await _ensure_editable(db, s, scope)
    await db.delete(s)
    await db.flush()


# ── 예정표 · 실행 이력 ────────────────────────────────────────────────────
#: 예정표를 한 번에 내다보는 상한. 화면은 7일을 쓴다.
OCCURRENCE_MAX_DAYS = 31


async def occurrences(
    db: AsyncSession, scope: VillageScope, start: dt.datetime, end: dt.datetime
) -> list[OccurrenceOut]:
    """[start, end) 의 예정 회차. 켜진 스케줄만. 실행기와 같은 규칙 함수를 쓴다."""
    if end <= start:
        return []
    if end - start > dt.timedelta(days=OCCURRENCE_MAX_DAYS):
        raise ApiError(
            f"예정표는 한 번에 {OCCURRENCE_MAX_DAYS}일까지 볼 수 있습니다.",
            code="OCCURRENCE_RANGE_TOO_WIDE",
        )
    schedules = await _visible_schedules(db, scope, enabled_only=True)
    names = await _file_names(db, [s.file_id for s in schedules])
    out: list[OccurrenceOut] = []
    for s in schedules:
        label = await _target_label(db, s.target_scope, s.target_ids)
        for at in rules.occurrences(rules.rule_of(s), start, end):
            out.append(
                OccurrenceOut(
                    schedule_id=s.id,
                    fire_at=at,
                    repeat=s.repeat,
                    target_label=label,
                    file_name=names.get(s.file_id),
                )
            )
    out.sort(key=lambda o: (o.fire_at, o.schedule_id))
    return out


async def list_runs(
    db: AsyncSession, schedule_id: int, scope: VillageScope, *, limit: int = 30
) -> list[ScheduleRunOut]:
    await _load(db, schedule_id, scope)
    rows = await db.scalars(
        select(ScheduleRun)
        .where(ScheduleRun.schedule_id == schedule_id)
        .order_by(ScheduleRun.fire_at.desc())
        .limit(limit)
    )
    return [ScheduleRunOut.model_validate(r) for r in rows.all()]
