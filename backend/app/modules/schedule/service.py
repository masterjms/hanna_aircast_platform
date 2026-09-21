"""자동방송 스케줄 서비스.

schedules / schedule_runs 테이블을 소유한다. 실행 시각 계산은 rules.py, 실제 실행은
app/tasks/schedule_runner.py 가 한다.

권한(스케줄 설계 2026-09-09):
  보기       대상이 내 범위와 하나라도 겹치면 — 진행 중 방송 가시성과 같은 판정
  수정·삭제  대상 **전체**가 내 범위 안일 때만. 군청이 건 관할 전체 스케줄을 이장이 지우면
             안 된다(방송 중지는 "보이면 멈춘다"가 맞지만 그건 긴급 제어다)
  만들기     대상이 내 범위 안. organization 은 내 관할 기관이어야

기관 대상은 마을이 아니라 **기관**으로도 본다(2026-09-15). 마을을 전부 다른 기관으로 옮겨
빈 기관이 되면 마을로는 아무에게도 안 겹쳐서, 만든 기관 관리자 화면에서 사라졌다 — 그
기관을 지우려 하면 보이지 않는 스케줄 때문에 막히는 일이 생긴다.
  보기       대상 기관 중 하나라도 내 관할이거나, 닿는 마을이 겹치면
  수정·삭제  대상 기관이 전부 내 관할일 때만(이장은 기관 대상을 못 고친다)

스케줄이 가리키는 기관·마을·단말은 지울 수 없다(ensure_not_schedule_target).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import SCHEDULE_MAX, ScheduleTarget
from app.core.orgtree import load_tree
from app.core.scope import VillageScope
from app.errors import (
    ApiError,
    DeviceNotFound,
    NotFound,
    OrganizationNotFound,
    OrganizationOutOfScope,
    ScheduleTargetInUse,
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
    """기관(자기 + 아래 전부, 깊이 무관) 소속 마을 id.

    실행 시점에 부르므로 새로 영입한 마을·새로 붙인 하위 기관도 자동으로 들어간다.
    """
    if not org_ids:
        return []
    tree = await load_tree(db)
    nodes = tree.subtree_of(org_ids)
    if not nodes:
        return []
    rows = await db.scalars(select(Village.id).where(Village.organization_id.in_(nodes)))
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


def _orgs_of(target_scope: str, target_ids: Sequence[str]) -> list[int] | None:
    """기관 대상이면 대상 기관 id, 아니면 None."""
    if target_scope == ScheduleTarget.ORGANIZATION.value:
        return _ints(target_ids)
    return None


def _visible(
    villages: set[int],
    scope: VillageScope,
    *,
    orgs: list[int] | None = None,
    org_ids: set[int] | None = None,
) -> bool:
    if scope.all_villages:
        return True
    if orgs and org_ids and any(o in org_ids for o in orgs):
        return True
    return any(scope.allows(v) for v in villages)


def _editable(
    villages: set[int],
    scope: VillageScope,
    *,
    orgs: list[int] | None = None,
    org_ids: set[int] | None = None,
) -> bool:
    if scope.all_villages:
        return True
    if orgs is not None:
        # 기관 대상은 기관으로 판정한다 — 마을이 비어도 만든 기관은 고칠 수 있고,
        # 마을이 전부 내 담당인 이장이라도 군청의 관할 전체 스케줄은 못 고친다.
        return bool(orgs) and org_ids is not None and all(o in org_ids for o in orgs)
    return bool(villages) and all(scope.allows(v) for v in villages)


def _visible_s(
    s: Schedule, villages: set[int], scope: VillageScope, org_ids: set[int] | None
) -> bool:
    return _visible(
        villages, scope, orgs=_orgs_of(s.target_scope, s.target_ids), org_ids=org_ids
    )


def _editable_s(
    s: Schedule, villages: set[int], scope: VillageScope, org_ids: set[int] | None
) -> bool:
    return _editable(
        villages, scope, orgs=_orgs_of(s.target_scope, s.target_ids), org_ids=org_ids
    )


# ── 대상 삭제 보호 ────────────────────────────────────────────────────────
async def schedules_targeting(
    db: AsyncSession, target_scope: str, ids: Sequence[str | int]
) -> list[str]:
    """이 기관·마을·단말을 직접 가리키는 스케줄의 설명("매일 07:00 안내.mp3").

    기관 대상 스케줄이 그 기관 아래 마을을 펼쳐 닿는 것은 세지 않는다 — 마을을 지워도
    기관 스케줄은 남은 마을로 계속 나간다. 직접 가리키는 것만 대상이 사라진다.
    """
    wanted: list[str | int] = []
    for i in ids:
        wanted.append(str(i))
        # 정본은 문자열이지만 손으로 넣은 행에 숫자가 섞여 있어도 놓치지 않는다.
        if isinstance(i, int) or str(i).isdigit():
            wanted.append(int(i))
    if not wanted:
        return []
    rows = (
        await db.execute(
            select(Schedule.repeat, Schedule.fire_time, Schedule.once_date, File.filename)
            .join(File, File.id == Schedule.file_id)
            .where(
                Schedule.target_scope == target_scope,
                or_(*(Schedule.target_ids.contains([w]) for w in wanted)),
            )
            .order_by(Schedule.fire_time, Schedule.id)
        )
    ).all()
    return [
        f"{rules.schedule_when(repeat, fire_time, once_date)} {filename}"
        for repeat, fire_time, once_date, filename in rows
    ]


async def ensure_not_schedule_target(
    db: AsyncSession, *, what: str, target_scope: str, ids: Sequence[str | int]
) -> None:
    """스케줄이 가리키는 대상이면 409. what 은 받침 있는 명사("기관"·"마을"·"단말")."""
    used = await schedules_targeting(db, target_scope, ids)
    if not used:
        return
    raise ScheduleTargetInUse(
        f"이 {what}을 대상으로 하는 자동방송이 {len(used)}건 있습니다 ({', '.join(used[:3])}"
        f"{' 외' if len(used) > 3 else ''}). "
        "자동방송 화면에서 그 스케줄을 먼저 지우거나 대상을 바꿔 주세요.",
        detail={"schedules": used},
    )


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


async def resolve_batch(
    db: AsyncSession, schedules: Sequence[Schedule]
) -> dict[int, tuple[set[int], str]]:
    """스케줄들의 (닿는 마을 집합, 사람이 읽는 이름)을 한 번에 만든다.

    건마다 구하면 규칙 100개에 질의가 103번 나간다(2026-09-10 실측). 종류별로 id 를
    모아 한 번씩만 읽어 규칙 수와 무관하게 6회 안팎으로 고정한다.

    **기관은 반드시 기관별로 분리한다.** 여러 기관의 마을을 한 덩어리로 합쳐서 쓰면
    A 기관 스케줄이 B 기관 마을에도 닿는 것으로 판정되어 권한이 넓어진다.

    결과는 target_villages()·_target_label() 을 건별로 부른 것과 같아야 한다 —
    테스트가 그 동등성을 본다.
    """
    if not schedules:
        return {}

    org_ids: set[int] = set()
    macs: set[str] = set()
    non_org: list[tuple[str, Sequence[str]]] = []
    for sch in schedules:
        if sch.target_scope == ScheduleTarget.ORGANIZATION.value:
            org_ids.update(_ints(sch.target_ids))
        else:
            non_org.append((sch.target_scope, sch.target_ids))
            if sch.target_scope == ScheduleTarget.DEVICE.value:
                macs.update(str(m) for m in sch.target_ids)

    # 기관 → (이름, 그 기관 아래 전부의 마을). expand_org_villages 와 같은 범위다.
    org_name: dict[int, str] = {}
    org_villages: dict[int, set[int]] = {oid: set() for oid in org_ids}
    if org_ids:
        tree = await load_tree(db)
        # 마디 → 그 마디를 부분 트리에 담는 대상 기관들. 대상 기관끼리 위아래면 한 마디가
        # 여러 대상에 들어간다 — 그래도 각 대상의 집합은 따로 유지한다.
        owner: dict[int, set[int]] = {}
        for target_org in org_ids:
            if target_org in tree:
                org_name[target_org] = tree.name[target_org]
            for node in tree.subtree(target_org):
                owner.setdefault(node, set()).add(target_org)
        if owner:
            for vid, v_org in (
                await db.execute(
                    select(Village.id, Village.organization_id).where(
                        Village.organization_id.in_(owner)
                    )
                )
            ).all():
                for target_org in owner.get(v_org, ()):
                    org_villages[target_org].add(vid)

    # 단말 → 마을. describe_targets 는 이름만 주므로 마을은 따로 읽는다.
    mac_village: dict[str, int | None] = {}
    if macs:
        mac_village = dict(
            (
                await db.execute(
                    select(Device.mac, Device.village_id).where(Device.mac.in_(macs))
                )
            ).all()
        )

    # 이름은 기존 함수를 그대로 쓴다 — 접는 규칙("가, 나 외 2곳")이 갈라지면 안 된다.
    non_org_labels = await device_service.describe_targets(db, non_org) if non_org else []

    out: dict[int, tuple[set[int], str]] = {}
    cursor = 0
    for sch in schedules:
        if sch.target_scope == ScheduleTarget.ORGANIZATION.value:
            ids = _ints(sch.target_ids)
            villages = set().union(*(org_villages.get(o, set()) for o in ids)) if ids else set()
            names = [org_name[o] for o in sorted(ids) if o in org_name]
            label = f"{', '.join(names)} 관할 전체" if names else ""
        else:
            label = non_org_labels[cursor]
            cursor += 1
            if sch.target_scope == ScheduleTarget.VILLAGE.value:
                villages = set(_ints(sch.target_ids))
            else:
                villages = {
                    v for v in (mac_village.get(str(m)) for m in sch.target_ids) if v is not None
                }
        out[sch.id] = (villages, label)
    return out


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


def _to_out(
    s: Schedule,
    scope: VillageScope,
    *,
    org_ids: set[int] | None,
    now: dt.datetime,
    file_names: dict[int, str],
    last_runs: dict[int, ScheduleRun],
    resolved: tuple[set[int], str],
) -> ScheduleOut:
    villages, label = resolved
    out = ScheduleOut.model_validate(s)
    out.file_name = file_names.get(s.file_id)
    out.target_label = label
    out.editable = _editable_s(s, villages, scope, org_ids)
    if s.enabled:
        out.next_fire_at = rules.next_occurrence(rules.rule_of(s), now)
    run = last_runs.get(s.id)
    out.last_run = ScheduleRunOut.model_validate(run) if run else None
    return out


async def _visible_schedules(
    db: AsyncSession,
    scope: VillageScope,
    *,
    org_ids: set[int] | None,
    enabled_only: bool = False,
) -> tuple[list[Schedule], dict[int, tuple[set[int], str]]]:
    """보이는 스케줄과 그 해석 결과(마을 집합·이름)를 함께 돌려준다.

    해석을 한 번만 하고 보이는지·고칠 수 있는지·이름에 모두 쓴다. 예전에는 마을 집합을
    두 번 구했다 — 보이는지 판정에 한 번, 고칠 수 있는지 판정에 또 한 번.

    마을 범위가 비어도 관할 기관이 있으면 기관 대상 스케줄은 보인다 — 마을을 옮기기 전의
    새 기관 관리자가 자기 기관에 건 스케줄을 볼 수 있어야 한다.
    """
    stmt = select(Schedule).order_by(Schedule.fire_time, Schedule.id)
    if enabled_only:
        stmt = stmt.where(Schedule.enabled.is_(True))
    rows = list((await db.scalars(stmt)).all())
    if not rows or (scope.is_empty and not org_ids):
        return [], {}

    resolved = await resolve_batch(db, rows)
    if scope.all_villages:
        return rows, resolved
    visible = [s for s in rows if _visible_s(s, resolved[s.id][0], scope, org_ids)]
    return visible, resolved


async def _file_names(db: AsyncSession, ids: Sequence[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await db.execute(select(File.id, File.filename).where(File.id.in_(set(ids))))
    return dict(rows.all())


async def list_schedules(
    db: AsyncSession, scope: VillageScope, *, org_ids: set[int] | None
) -> list[ScheduleOut]:
    schedules, resolved = await _visible_schedules(db, scope, org_ids=org_ids)
    now = dt.datetime.now(dt.timezone.utc)
    names = await _file_names(db, [s.file_id for s in schedules])
    runs = await _last_runs(db, [s.id for s in schedules])
    return [
        _to_out(
            s,
            scope,
            org_ids=org_ids,
            now=now,
            file_names=names,
            last_runs=runs,
            resolved=resolved[s.id],
        )
        for s in schedules
    ]


async def get_schedule(
    db: AsyncSession, schedule_id: int, scope: VillageScope, *, org_ids: set[int] | None
) -> ScheduleOut:
    s = await _load(db, schedule_id, scope, org_ids)
    now = dt.datetime.now(dt.timezone.utc)
    names = await _file_names(db, [s.file_id])
    runs = await _last_runs(db, [s.id])
    resolved = (await resolve_batch(db, [s]))[s.id]
    return _to_out(
        s,
        scope,
        org_ids=org_ids,
        now=now,
        file_names=names,
        last_runs=runs,
        resolved=resolved,
    )


async def _load(
    db: AsyncSession, schedule_id: int, scope: VillageScope, org_ids: set[int] | None
) -> Schedule:
    s = await db.get(Schedule, schedule_id)
    if s is None:
        raise ScheduleNotFound()
    villages = await target_villages(db, s.target_scope, s.target_ids)
    if not _visible_s(s, villages, scope, org_ids):
        raise ScheduleNotFound()
    return s


async def _ensure_editable(
    db: AsyncSession, s: Schedule, scope: VillageScope, org_ids: set[int] | None
) -> None:
    villages = await target_villages(db, s.target_scope, s.target_ids)
    if not _editable_s(s, villages, scope, org_ids):
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
        once_date=payload.once_date,
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
    return await get_schedule(db, s.id, scope, org_ids=org_ids)


async def update_schedule(
    db: AsyncSession,
    schedule_id: int,
    payload: ScheduleUpdate,
    *,
    scope: VillageScope,
    org_ids: set[int] | None,
) -> ScheduleOut:
    s = await _load(db, schedule_id, scope, org_ids)
    await _ensure_editable(db, s, scope, org_ids)
    data = payload.model_dump(exclude_unset=True)

    # 켜기·끄기만 바꾸는 경우가 대부분이다. 그때는 모양 검사를 다시 하지 않는다.
    if set(data) <= {"enabled", "store_flash"}:
        for k, v in data.items():
            setattr(s, k, v)
        await db.flush()
        return await get_schedule(db, s.id, scope, org_ids=org_ids)

    # 나머지는 바꾼 뒤의 전체 모양을 ScheduleCreate 검증기로 다시 통과시킨다 —
    # 매주로 바꾸면서 요일을 안 주면 영영 안 나가는 규칙이 되는 것을 막는다.
    merged = {
        "repeat": s.repeat,
        "weekdays": s.weekdays,
        "month_days": s.month_days,
        "year_dates": s.year_dates,
        "once_date": s.once_date,
        "fire_time": s.fire_time,
        "file_id": s.file_id,
        "target_scope": s.target_scope,
        "target_ids": s.target_ids,
        "store_flash": s.store_flash,
        "enabled": s.enabled,
    }
    clearable = {"weekdays", "month_days", "year_dates", "once_date"}
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
            await target_villages(db, checked.target_scope.value, checked.target_ids),
            scope,
            orgs=_orgs_of(checked.target_scope.value, checked.target_ids),
            org_ids=org_ids,
        ):
            raise ApiError(
                "대상 전체가 내 관할이어야 합니다.", code="SCHEDULE_NOT_EDITABLE"
            )

    s.repeat = checked.repeat.value
    s.weekdays = checked.weekdays
    s.month_days = checked.month_days
    s.year_dates = [d.model_dump() for d in checked.year_dates] if checked.year_dates else None
    s.once_date = checked.once_date
    s.fire_time = checked.fire_time
    s.file_id = checked.file_id
    s.target_scope = checked.target_scope.value
    s.target_ids = list(checked.target_ids)
    s.store_flash = checked.store_flash
    s.enabled = checked.enabled
    await db.flush()
    return await get_schedule(db, s.id, scope, org_ids=org_ids)


async def delete_schedule(
    db: AsyncSession, schedule_id: int, *, scope: VillageScope, org_ids: set[int] | None
) -> None:
    s = await _load(db, schedule_id, scope, org_ids)
    await _ensure_editable(db, s, scope, org_ids)
    await db.delete(s)
    await db.flush()


# ── 예정표 · 실행 이력 ────────────────────────────────────────────────────
#: 예정표를 한 번에 내다보는 상한. 화면은 7일을 쓴다.
OCCURRENCE_MAX_DAYS = 31


async def occurrences(
    db: AsyncSession,
    scope: VillageScope,
    start: dt.datetime,
    end: dt.datetime,
    *,
    org_ids: set[int] | None,
) -> list[OccurrenceOut]:
    """[start, end) 의 예정 회차. 켜진 스케줄만. 실행기와 같은 규칙 함수를 쓴다."""
    if end <= start:
        return []
    if end - start > dt.timedelta(days=OCCURRENCE_MAX_DAYS):
        raise ApiError(
            f"예정표는 한 번에 {OCCURRENCE_MAX_DAYS}일까지 볼 수 있습니다.",
            code="OCCURRENCE_RANGE_TOO_WIDE",
        )
    schedules, resolved = await _visible_schedules(
        db, scope, org_ids=org_ids, enabled_only=True
    )
    names = await _file_names(db, [s.file_id for s in schedules])
    out: list[OccurrenceOut] = []
    for s in schedules:
        label = resolved[s.id][1]
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
    db: AsyncSession,
    schedule_id: int,
    scope: VillageScope,
    *,
    org_ids: set[int] | None,
    limit: int = 30,
) -> list[ScheduleRunOut]:
    await _load(db, schedule_id, scope, org_ids)
    rows = await db.scalars(
        select(ScheduleRun)
        .where(ScheduleRun.schedule_id == schedule_id)
        .order_by(ScheduleRun.fire_at.desc())
        .limit(limit)
    )
    return [ScheduleRunOut.model_validate(r) for r in rows.all()]
