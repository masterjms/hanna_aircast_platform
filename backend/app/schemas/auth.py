"""인증 스키마."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from app.constants import Role
from app.schemas.common import ApiModel


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)
    #: 2단계 인증 숫자 6자리 또는 복구 코드(XXXX-XXXX). 켠 계정만 필요(문제점 70번 2항).
    otp: str | None = Field(default=None, max_length=20)


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
    #: 소속 기관. 기관 관리자만 있다. 상단 "담당 범위" 표시와 지역 관리 트리의 뿌리 판정용.
    organization_id: int | None = None
    organization_name: str | None = None
    #: 임시 비밀번호 계정. 화면은 이 값이면 비밀번호 변경 화면만 보여준다(향후검토 10번).
    must_change_password: bool = False
    #: 2단계 인증을 켰는가 / 역할상 필수인데 아직 등록 안 했는가(그러면 등록 화면만 쓴다).
    otp_enabled: bool = False
    otp_required: bool = False
    must_enroll_otp: bool = False


class OtpSetupOut(ApiModel):
    """등록 시작 — OTP 앱에 넣을 비밀과 QR 주소. 이 응답에서만 나온다."""

    secret: str
    otpauth_uri: str


class OtpCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class OtpEnabledOut(ApiModel):
    """켜기 완료 — 복구 코드 10개(이 응답에서 한 번만)."""

    recovery_codes: list[str]


class OtpDisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    """자기 비밀번호 변경. 임시 비밀번호 계정의 첫 로그인에서 반드시 거친다."""

    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=64)


class LoginEventOut(ApiModel):
    """로그인 기록 한 줄(문제점 65번)."""

    id: int
    user_id: int | None
    username: str
    ip: str | None
    user_agent: str | None
    #: ok · bad_password · unknown_user · expired
    result: str
    logged_in_at: dt.datetime
    logged_out_at: dt.datetime | None


class LoginEventPage(BaseModel):
    total: int
    page: int
    size: int
    items: list[LoginEventOut]


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: MeResponse
