"""방송 기록 API — GET /api/events (문제점 50번)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, Db, Scope
from app.modules.history import service
from app.schemas.history import HistoryPage

router = APIRouter(tags=["history"])


@router.get("/api/events", response_model=HistoryPage)
async def list_events(
    db: Db,
    scope: Scope,
    _: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    size: int = 10,
    kind: Annotated[str | None, Query(pattern="^(file|schedule|live|ota)$")] = None,
    q: Annotated[str | None, Query(max_length=60)] = None,
    date_from: Annotated[dt.date | None, Query(alias="from")] = None,
    date_to: Annotated[dt.date | None, Query(alias="to")] = None,
) -> HistoryPage:
    """단말별 방송 기록. size 는 10·20·50 중 하나(다른 값은 10). 날짜는 KST 기준 하루 단위."""
    return await service.page(
        db, scope, page_no=page, size=size, kind=kind, q=q, date_from=date_from, date_to=date_to
    )
