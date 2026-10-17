"""관리 행위 감사 기록 (문제점 70번 10/15 ①, 2026-10-17).

한 줄 = 화면·API 의 변경 요청 하나(POST·PUT·PATCH·DELETE). 누가(계정·역할·IP)·언제·무엇을(행위 이름,
대상, 요청 내용 요약)·결과(HTTP 상태)를 남긴다. 실패·권한 없음도 남긴다 — 시도 자체가 기록할 일이다.
비밀번호·토큰·키는 요약에서 지운다. 로그인·로그아웃은 login_events 가 따로 남긴다.

위·변조 방지(70번 ④): chain_seq·prev_hash·row_hash 는 DB 트리거가 채운다(0028). 앞 줄 해시를 이어
붙여 한 줄이라도 고치거나 빼면 그 뒤가 전부 어긋난다. 같은 트리거가 수정을 막고, 삭제는 1년이 지난
줄(보관 기간 정리)만 허락한다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    #: 계정을 지워도 기록은 남아야 하므로 FK 를 걸지 않고 이름·역할을 함께 적는다.
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    username: Mapped[str | None] = mapped_column(String(50))
    role: Mapped[str | None] = mapped_column(String(20))
    ip: Mapped[str | None] = mapped_column(String(45))
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    #: 사람이 읽는 행위 이름(「계정 추가」·「단말 삭제」…). 표에 없는 경로는 method + path.
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    #: 경로의 대상(계정 id·MAC·지역 id 등). 없으면 NULL.
    target: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: 요청 내용 요약(JSON 본문에서 비밀값을 지운 것, 2KB 이내).
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    chain_seq: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    row_hash: Mapped[str | None] = mapped_column(String(64))
