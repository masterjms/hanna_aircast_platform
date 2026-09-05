"""조직 · 권한 — villages, zones, users, user_villages."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    #: 법정동코드 10자리(리까지). 주소 검색이 자동으로 채우고, 지도의 리 경계
    #: 도형(TL_SCCO_LI)과의 조인 키다. village_id(방송 그룹)와는 무관한 속성.
    #: ⚠ 행정구역 개편으로 코드가 바뀔 수 있다(2026년 전남광주 통합 실측) —
    #:   바뀌면 이 속성만 갱신하고 village_id 는 그대로 둔다(지도 설계 §1.3).
    b_code: Mapped[str | None] = mapped_column(String(10))
    #: MQTT village_id — 법정동코드(10) + 마을 연번(2) = 12자리(레지스트리 사양 §2.4).
    #: b_code 가 처음 채워질 때 한 번 만들고 그 뒤에는 바꾸지 않는다(행정구역 개편에도).
    #: NULL 이면(주소 없는 마을) 예전 방식 id 8자리를 쓴다 — app/core/village_token.py.
    village_code: Mapped[str | None] = mapped_column(String(12), unique=True)
    #: 대표 주소. 주소 검색이 도로명·지번을 같이 채운다 — 농촌은 두 표기가 병용된다.
    road_address: Mapped[str | None] = mapped_column(String(255))
    jibun_address: Mapped[str | None] = mapped_column(String(255))
    #: 마을 경계 폴리곤(GeoJSON geometry, WGS84). 「구역의 도형」의 리 경계를
    #: b_code 로 조인해 넣는다 — scripts/import_boundaries.py 참고.
    #: PostGIS 를 쓰지 않는다: 그리기만 하고 공간 질의는 하지 않는다.
    boundary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
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
