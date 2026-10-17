"""감사 기록 스키마 (문제점 70번 10/15 ①④)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel

from app.schemas.common import ApiModel


class AuditEventOut(ApiModel):
    id: int
    at: dt.datetime
    user_id: int | None
    username: str | None
    role: str | None
    ip: str | None
    method: str
    path: str
    action: str
    target: str | None
    status: int
    detail: dict[str, Any] | None


class AuditEventPage(BaseModel):
    total: int
    page: int
    size: int
    items: list[AuditEventOut]


class ChainCheck(BaseModel):
    """한 기록(접속기록·감사 기록)의 해시 체인 확인 결과."""

    table: str
    label: str
    rows: int
    ok: bool
    #: 처음 어긋난 줄의 순번. ok 면 None.
    first_bad_seq: int | None = None
    #: 체인 밖에 끼어든 줄(트리거를 끄고 넣은 줄) 수.
    unchained: int = 0
    last_seq: int | None = None
    last_hash: str | None = None
    #: 지난 점검 때 적어 둔 끝 해시(앵커)와 다른 순번. 통째로 다시 계산해 덮은 흔적.
    anchor_mismatch: list[int] = []


class VerifyResult(BaseModel):
    ok: bool
    checked_at: dt.datetime
    chains: list[ChainCheck]
    #: 마지막 점검 시각과 점검한 사람(없으면 None).
    last_review_at: dt.datetime | None = None
    last_review_by: str | None = None
