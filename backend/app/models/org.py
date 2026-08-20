"""조직 · 권한 — villages, zones, users, user_villages."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import Role
from app.models.base import Base

_ROLES = ", ".join(f"'{r.value}'" for r in Role)


class Village(Base):
    """마을.

    id 는 DB 일련번호이고, MQTT 로 나갈 때만 8자리 문자열로 변환한다
    (app.mqtt.topics.village_token). DB 안에 문자열 버전을 따로 두지 않는다.
    """

    __tablename__ = "villages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sido: Mapped[str | None] = mapped_column(String(50))
    sigungu: Mapped[str | None] = mapped_column(String(50))
    address_detail: Mapped[str | None] = mapped_column(String(255))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Zone(Base):
    """구역. 마을 하위 단위이며 단말까지 전달되지 않는다.

    구역 방송은 서버가 소속 단말 MAC 목록으로 펼쳐서 개별 발행한다.
    """

    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    village_id: Mapped[int] = mapped_column(
        ForeignKey("villages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    address_detail: Mapped[str | None] = mapped_column(String(255))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(Base):
    """관리자 계정.

    super_admin 은 user_villages 를 보지 않고 전체 접근한다(role 로 판정).
    village_admin 만 user_villages 로 담당 마을을 제한받는다.
    """

    __tablename__ = "users"
    __table_args__ = (CheckConstraint(f"role IN ({_ROLES})", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserVillage(Base):
    """계정 ↔ 담당 마을 (다대다). village_admin 전용."""

    __tablename__ = "user_villages"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    village_id: Mapped[int] = mapped_column(
        ForeignKey("villages.id", ondelete="CASCADE"), primary_key=True
    )
