"""로그인 기록 (문제점 65번, 2026-10-09).

한 줄 = 로그인 시도 하나. 성공이면 로그아웃 때 logged_out_at 이 채워진다(토큰은 상태가 없어
「로그아웃」 버튼을 누른 경우만 알 수 있다 — 창을 그냥 닫으면 비어 있다). 실패도 남긴다(비밀번호
틀림·없는 아이디·기간 만료) — 무차별 대입 흔적이 여기 보인다. 비밀번호는 어떤 형태로도 적지 않는다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LoginEvent(Base):
    __tablename__ = "login_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    #: 계정을 지워도 기록은 남아야 하므로 이름을 따로 적는다.
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    #: ok · bad_password · unknown_user · expired
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    logged_in_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    logged_out_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
