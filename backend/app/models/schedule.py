"""자동방송 스케줄 — schedules, schedule_runs.

규칙 한 줄(schedules)만 저장하고 개별 실행 날짜는 저장하지 않는다 — cron 과 같다.
"언제 나가나"는 app/modules/schedule/rules.py 가 계산한다. 나간 뒤의 기록만
schedule_runs 에 남는다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import Repeat, ScheduleTarget
from app.models.base import Base

_SCOPES = ", ".join(f"'{s.value}'" for s in ScheduleTarget)
_REPEATS = ", ".join(f"'{r.value}'" for r in Repeat)


class Schedule(Base):
    """자동방송 규칙 하나.

    매일·매주·매월·매년 중 하나이고 시각은 하나다(둘 원하면 규칙 둘). 시각은 KST.
    대상은 마을·단말·기관(관할 전체) 중 하나 — 기관은 실행 시점에 마을로 펼치므로
    나중에 영입한 마을도 자동으로 들어간다.
    """

    __tablename__ = "schedules"
    __table_args__ = (
        CheckConstraint(f"target_scope IN ({_SCOPES})", name="ck_schedules_target_scope"),
        CheckConstraint(f"repeat IN ({_REPEATS})", name="ck_schedules_repeat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    repeat: Mapped[str] = mapped_column(String(10), nullable=False)
    #: weekly — 0=일 … 6=토
    weekdays: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    #: monthly — 1~31. 그 날이 없는 달은 건너뛴다.
    month_days: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    #: yearly — [{"month": m, "day": d}, …]
    year_dates: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    #: 하루 중 시각(KST). timezone 없는 TIME 이고 그 값이 곧 한국 시각이다.
    fire_time: Mapped[dt.time] = mapped_column(Time, nullable=False)

    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False)

    target_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    #: village 면 마을 id, device 면 MAC, organization 이면 기관 id 목록.
    target_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    #: 단말 플래시에 저장할지. 반복 재생하는 안내음성이라 켜는 쪽이 유리할 수 있다.
    store_flash: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ScheduleRun(Base):
    """규칙이 실제로 걸린 회차의 기록 — "나갔다 / 안 나갔다".

    (schedule_id, fire_at) 유니크가 중복 실행 방지의 실체다. 실행기는 방송을 걸기 **전에**
    이 행을 넣고, 유니크 충돌이면 다른 tick 이나 다른 프로세스가 이미 처리한 것이므로
    물러난다. 서버가 재시작해도, 같은 분에 tick 이 두 번 돌아도 한 번만 나간다.
    """

    __tablename__ = "schedule_runs"
    __table_args__ = (
        UniqueConstraint("schedule_id", "fire_at", name="uq_schedule_runs_schedule_fire"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 규칙상 나갔어야 하는 시각(KST 로 계산해 UTC 로 저장).
    fire_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: started | skipped | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: skipped·failed 의 사유. 화면이 "왜 안 나갔지" 에 답하는 값.
    reason: Mapped[str | None] = mapped_column(String(200))
    #: started 면 만들어진 방송 이벤트.
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("broadcast_events.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
