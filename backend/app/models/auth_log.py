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

    #: 위·변조 방지 해시 체인(0028, 문제점 70번 10/15 ④) — DB 트리거가 채운다. 해시에는 바뀔 수 있는
    #: 칸(logged_out_at, 계정 삭제 때 NULL 이 되는 user_id)을 넣지 않는다.
    #: models/audit.py 와 같은 방식.
    chain_seq: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    row_hash: Mapped[str | None] = mapped_column(String(64))
