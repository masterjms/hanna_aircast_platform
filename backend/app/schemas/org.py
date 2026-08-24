"""마을 · 구역 · 계정 스키마."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field, field_validator

from app.constants import Role
from app.schemas.common import ApiModel


# ── 마을 ─────────────────────────────────────────────────────────────────
class VillageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    sido: str | None = Field(default=None, max_length=50)
    sigungu: str | None = Field(default=None, max_length=50)
    address_detail: str | None = Field(default=None, max_length=255)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class VillageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    sido: str | None = Field(default=None, max_length=50)
    sigungu: str | None = Field(default=None, max_length=50)
    address_detail: str | None = Field(default=None, max_length=255)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class VillageOut(ApiModel):
    id: int
    name: str
    sido: str | None
    sigungu: str | None
    address_detail: str | None
    lat: float | None
    lng: float | None
    created_at: dt.datetime
    #: MQTT 로 나가는 8자리 표현. 디버깅할 때 화면에서 바로 보이면 편하다.
    village_token: str = ""
    #: 등록된 단말 수(설치 현황).
    device_count: int = 0
    #: 그중 지금 온라인인 수. 방송은 온라인 단말에만 나가므로 화면에서 둘을
    #: 같이 보여줘야 한다 — 등록 대수만 보면 "3대에 나가겠구나" 하고 눌렀는데
    #: 1대만 나가는 상황이 생긴다.
    online_count: int = 0


# ── 구역 ─────────────────────────────────────────────────────────────────
class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    address_detail: str | None = Field(default=None, max_length=255)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class ZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    address_detail: str | None = Field(default=None, max_length=255)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class ZoneOut(ApiModel):
    id: int
    village_id: int
    name: str
    address_detail: str | None
    lat: float | None
    lng: float | None
    created_at: dt.datetime
    device_count: int = 0
    online_count: int = 0


# ── 계정 ─────────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    #: bcrypt 가 72바이트에서 자르므로 그 아래로 제한한다.
    password: str = Field(min_length=8, max_length=64)
    role: Role
    village_ids: list[int] = Field(default_factory=list)

    @field_validator("village_ids")
    @classmethod
    def _dedupe(cls, v: list[int]) -> list[int]:
        return sorted(set(v))


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=64)
    role: Role | None = None
    village_ids: list[int] | None = None


class UserOut(ApiModel):
    id: int
    username: str
    role: Role
    created_at: dt.datetime
    village_ids: list[int] = Field(default_factory=list)
