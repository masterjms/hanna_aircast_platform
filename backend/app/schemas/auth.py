"""인증 스키마."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.constants import Role
from app.schemas.common import ApiModel


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class VillageBrief(ApiModel):
    id: int
    name: str


class MeResponse(ApiModel):
    id: int
    username: str
    role: Role
    #: 상단바의 "담당 범위" 표시에 쓴다. super_admin 이면 전체 마을이 들어온다.
    villages: list[VillageBrief]
    all_villages: bool
    device_count: int


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: MeResponse
