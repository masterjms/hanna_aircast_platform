"""자동방송 스케줄 라우터.

권한은 service 가 판정한다 — 보기는 범위가 겹치면, 수정·삭제는 대상 전체가 범위 안일 때만.
기관 대상 스케줄은 기관으로도 판정하므로 모든 경로가 관할 기관(org_ids)을 함께 넘긴다.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, Db, OrgIds, Scope
from app.errors import ApiError
from app.modules.schedule import service
from app.schemas.schedule import (
    OccurrenceOut,
    ScheduleCreate,
    ScheduleOut,
    ScheduleRunOut,
    ScheduleUpdate,
)

router = APIRouter(prefix="/api/schedules", tags=["schedule"])


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(db: Db, scope: Scope, org_ids: OrgIds) -> list[ScheduleOut]:
    """내 범위와 겹치는 스케줄. editable 로 고칠 수 있는지 알려준다."""
    return await service.list_schedules(db, scope, org_ids=org_ids)


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    payload: ScheduleCreate, db: Db, user: CurrentUser, scope: Scope, org_ids: OrgIds
) -> ScheduleOut:
    return await service.create_schedule(
        db, payload, actor_id=user.id, scope=scope, org_ids=org_ids
    )


@router.get("/occurrences", response_model=list[OccurrenceOut])
async def occurrences(
    db: Db,
    scope: Scope,
    org_ids: OrgIds,
    from_: Annotated[dt.datetime | None, Query(alias="from")] = None,
    to: dt.datetime | None = None,
) -> list[OccurrenceOut]:
    """예정표·오늘 일정. 기본은 지금부터 7일. 시각은 timezone 을 붙여 보낸다."""
    now = dt.datetime.now(dt.timezone.utc)
    start = from_ or now
    end = to or (start + dt.timedelta(days=7))
    if start.tzinfo is None or end.tzinfo is None:
        raise ApiError("from/to 는 timezone 이 있어야 합니다.", code="VALIDATION_FAILED")
    return await service.occurrences(db, scope, start, end, org_ids=org_ids)


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(schedule_id: int, db: Db, scope: Scope, org_ids: OrgIds) -> ScheduleOut:
    return await service.get_schedule(db, schedule_id, scope, org_ids=org_ids)


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: int, payload: ScheduleUpdate, db: Db, scope: Scope, org_ids: OrgIds
) -> ScheduleOut:
    return await service.update_schedule(db, schedule_id, payload, scope=scope, org_ids=org_ids)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(schedule_id: int, db: Db, scope: Scope, org_ids: OrgIds) -> None:
    await service.delete_schedule(db, schedule_id, scope=scope, org_ids=org_ids)


@router.get("/{schedule_id}/runs", response_model=list[ScheduleRunOut])
async def list_runs(
    schedule_id: int, db: Db, scope: Scope, org_ids: OrgIds
) -> list[ScheduleRunOut]:
    """최근 실행 결과 — 나갔다·건너뛰었다·실패했다와 그 사유."""
    return await service.list_runs(db, schedule_id, scope, org_ids=org_ids)
