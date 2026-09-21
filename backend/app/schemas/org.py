"""마을 · 구역 · 계정 스키마."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.constants import Role
from app.schemas.common import ApiModel


# ── 기관 (관리자 계층 설계 §3 · v2 깊이 무제한) ─────────────────────────
class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    #: 붙일 자리. null 이면 뿌리(최고 관리자만). 기관 관리자는 자기 관할 안의 마디여야 한다.
    parent_id: int | None = None


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    #: 옮기기. 자기 자신·자기 아래로는 못 옮긴다(ORG_CYCLE). 출발·도착 모두 관할이어야 한다.
    parent_id: int | None = None


class OrganizationOut(ApiModel):
    id: int
    name: str
    parent_id: int | None
    parent_name: str | None = None
    #: 바로 아래 것들의 수. 삭제 가능 여부(셋 다 0)를 화면이 안다. 부분 트리 합계는
    #: 화면이 목록으로 계산한다 — 여기서 재귀 합계를 내면 마디마다 질의가 는다.
    village_count: int = 0
    user_count: int = 0
    child_count: int = 0
    created_at: dt.datetime


# ── 마을 ─────────────────────────────────────────────────────────────────
class VillageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    #: 관리 기관(트리의 마디). 기관 관리자가 비우면 자기 기관이 된다. null 은 최고 관리자만.
    organization_id: int | None = None
    sido: str | None = Field(default=None, max_length=50)
    sigungu: str | None = Field(default=None, max_length=50)
    address_detail: str | None = Field(default=None, max_length=255)
    # 아래 넷은 주소 검색(GET /api/geo/address) 결과에서 그대로 옮겨 넣는다 —
    # 사람이 치지 않는다. b_code 는 리 경계 도형과의 조인 키(지도 설계 §2).
    b_code: str | None = Field(default=None, min_length=10, max_length=10, pattern=r"^\d{10}$")
    road_address: str | None = Field(default=None, max_length=255)
    jibun_address: str | None = Field(default=None, max_length=255)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class VillageUpdate(BaseModel):
    #: 관리 기관 변경(설계 §6.2). 출발·도착 기관이 모두 관할이어야 한다.
    organization_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    #: 경계 폴리곤(GeoJSON geometry, WGS84). scripts/import_boundaries.py 가 넣는다.
    #: 사람이 화면에서 입력하는 값이 아니라 VillageCreate 에는 두지 않는다.
    boundary: dict[str, Any] | None = None
    sido: str | None = Field(default=None, max_length=50)
    sigungu: str | None = Field(default=None, max_length=50)
    address_detail: str | None = Field(default=None, max_length=255)
    b_code: str | None = Field(default=None, min_length=10, max_length=10, pattern=r"^\d{10}$")
    road_address: str | None = Field(default=None, max_length=255)
    jibun_address: str | None = Field(default=None, max_length=255)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class VillageOut(ApiModel):
    id: int
    name: str
    sido: str | None
    sigungu: str | None
    address_detail: str | None
    b_code: str | None = None
    road_address: str | None = None
    jibun_address: str | None = None
    lat: float | None
    lng: float | None
    #: 경계가 들어와 있는지만 화면에 알려준다. 도형 자체는 지도 API 로 내려간다
    #: (목록 응답마다 수십 KB 를 실으면 마을 관리 화면이 무거워진다).
    has_boundary: bool = False
    created_at: dt.datetime
    #: 법정동코드(10)+연번(2) 12자리. 주소가 없어 못 만든 마을은 null.
    village_code: str | None = None
    #: MQTT 로 나가는 village_id 문자열 — village_code, 없으면 예전 방식 id 8자리.
    village_token: str = ""
    #: 관리 기관(설계 §3). null 이면 최고 관리자만 보는 마을.
    organization_id: int | None = None
    organization_name: str | None = None
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
#: 계정 사용 기간(일). 문제점 26번 — 1~30일, 기본 15일.
VALID_DAYS_MIN = 1
VALID_DAYS_MAX = 30
VALID_DAYS_DEFAULT = 15


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    #: bcrypt 가 72바이트에서 자르므로 그 아래로 제한한다.
    password: str = Field(min_length=8, max_length=64)
    role: Role
    village_ids: list[int] = Field(default_factory=list)
    #: org_admin 의 소속 기관. 그 밖의 역할은 null 이어야 한다.
    organization_id: int | None = None
    #: 계정 사용 기간(일). 만료 다음 날부터 정리 작업이 계정을 지운다.
    #: null 은 무기한 — 상시 운영 계정을 만들 때만 쓴다. 화면은 기본 15일을 채운다.
    valid_days: int | None = Field(
        default=VALID_DAYS_DEFAULT, ge=VALID_DAYS_MIN, le=VALID_DAYS_MAX
    )

    @field_validator("village_ids")
    @classmethod
    def _dedupe(cls, v: list[int]) -> list[int]:
        return sorted(set(v))


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=64)
    role: Role | None = None
    village_ids: list[int] | None = None
    organization_id: int | None = None
    #: 보내면 오늘부터 다시 센다(기간 연장). null 을 명시하면 무기한이 된다.
    #: 아예 빼면 만료일을 건드리지 않는다.
    valid_days: int | None = Field(default=None, ge=VALID_DAYS_MIN, le=VALID_DAYS_MAX)


class UserOut(ApiModel):
    id: int
    username: str
    role: Role
    #: 만료 시각. null 이면 무기한.
    expires_at: dt.datetime | None = None
    created_at: dt.datetime
    village_ids: list[int] = Field(default_factory=list)
    organization_id: int | None = None
    organization_name: str | None = None
    #: 임시 비밀번호를 받고 아직 바꾸지 않은 계정. 계정 목록에 「변경 대기」로 보인다.
    must_change_password: bool = False


class TempPasswordOut(ApiModel):
    """임시 비밀번호. 이 응답에서 한 번만 나오고 서버에는 해시만 남는다."""

    password: str
