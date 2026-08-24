"""자동방송 스케줄 — schedules."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import TargetScope
from app.models.base import Base

_SCOPES = ", ".join(f"'{s.value}'" for s in TargetScope)


class Schedule(Base):
    """자동방송 규칙.

    최대 10개 제한은 DB 제약이 아니라 API 레벨에서 본다.
    APScheduler 가 1분 주기로 이 표를 훑어 해당 시각의 규칙을 실행한다.
    """

    __tablename__ = "schedules"
    __table_args__ = (
        CheckConstraint(f"target_scope IN ({_SCOPES})", name="ck_schedules_target_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: 1~12
    months: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    #: 0=일 ~ 6=토
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    times: Mapped[list[dt.time]] = mapped_column(ARRAY(Time), nullable=False)

    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False)

    target_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    #: scope 에 맞는 id 목록 (broadcast_events.target_ids 와 동일 규칙).
    target_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
