"""FastAPI 의존성 — 인증과 권한.

라우터가 쓰는 것:

    CurrentUser      로그인한 계정 (없으면 401)
    Scope            담당 마을 범위 (조회 필터 · 대상 검사)
    OrgIds           이 계정이 다스리는 기관 id 집합 (최고 관리자는 None=전체)
    OrgAdmin         기관 관리자 이상 가드 (기관·마을·계정 관리)
    SuperAdmin       super_admin 전용 라우트 가드

범위 산출 규칙은 app/core/authz.py — 관리자 계층 설계(2026-09-08) §4.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import Role
from app.core import authz
from app.core.scope import VillageScope
from app.core.security import decode_access_token, verify_password
from app.db import get_db
from app.errors import (
    ConfirmPasswordRequired,
    ConfirmPasswordWrong,
    OrgAdminRequired,
    OtpEnrollRequired,
    PasswordChangeRequired,
    SessionReplaced,
    SuperAdminRequired,
    Unauthorized,
)
from app.live.registry import LiveRegistry
from app.models.org import User
from app.mqtt.publisher import MqttPublisher

# auto_error=False 로 두고 401 을 우리 에러 규약으로 직접 던진다.
_bearer = HTTPBearer(auto_error=False)


#: 임시 비밀번호 계정도 부를 수 있는 경로 — 자기 정보 확인, 비밀번호 변경, 로그아웃.
PASSWORD_CHANGE_ALLOWED_PATHS = frozenset(
    {"/api/auth/me", "/api/auth/password", "/api/auth/logout"}
)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None:
        raise Unauthorized()

    payload = decode_access_token(credentials.credentials)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Unauthorized() from exc

    user = await db.get(User, user_id)
    if user is None:
        # 토큰은 유효하지만 계정이 삭제된 경우.
        raise Unauthorized()
    _check_session(payload, user)
    # 토큰 유효기간이 계정 만료보다 길 수 있다. 이미 발급된 토큰도 막는다.
    if user.is_expired():
        raise Unauthorized()
    # 임시 비밀번호 계정은 새 비밀번호를 정하기 전까지 다른 기능을 못 쓴다(향후검토 10번).
    # 화면만 막으면 임시 비밀번호를 아는 사람이 API 로 그냥 쓴다 — 서버에서 막는다.
    if user.must_change_password and request.url.path not in PASSWORD_CHANGE_ALLOWED_PATHS:
        raise PasswordChangeRequired()
    # 2단계 인증이 필수인 역할인데 아직 등록하지 않았으면 등록만 하게 한다(문제점 70번 2항).
    if must_enroll_otp(user) and request.url.path not in OTP_ENROLL_ALLOWED_PATHS:
        raise OtpEnrollRequired()
    return user


def _check_session(payload: dict, user: User) -> None:
    """토큰의 세션 키가 계정의 지금 세션과 같아야 한다(동시 접속 제한, 문제점 70번 10/15 ③).

    다른 곳에서 새로 로그인하면 session_key 가 바뀌어 먼저 쓰던 토큰은 다음 요청에서 401.
    로그아웃하면 session_key 를 비우므로 그 토큰도 더 못 쓴다. 0028 전에 받은 토큰(sid 없음)은
    session_key 가 비어 있는 동안만 통한다.
    """
    if not settings.single_session:
        return
    if payload.get("sid") != user.session_key:
        raise SessionReplaced() if user.session_key else Unauthorized()


#: 2단계 인증 등록 전에도 부를 수 있는 경로
OTP_ENROLL_ALLOWED_PATHS = PASSWORD_CHANGE_ALLOWED_PATHS | {
    "/api/auth/otp/setup",
    "/api/auth/otp/enable",
}


def otp_required(user: User) -> bool:
    roles = {r.strip() for r in settings.totp_required_roles.split(",") if r.strip()}
    return user.role in roles


def must_enroll_otp(user: User) -> bool:
    return otp_required(user) and user.totp_enabled_at is None


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_village_scope(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> VillageScope:
    """계정의 마을 범위. 역할마다 다르게 풀린다(authz.resolve_scope)."""
    return await authz.resolve_scope(db, user)


Scope = Annotated[VillageScope, Depends(get_village_scope)]


async def get_org_ids(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> set[int] | None:
    return await authz.org_ids_under(db, user)


OrgIds = Annotated[set[int] | None, Depends(get_org_ids)]


async def require_super_admin(user: CurrentUser) -> User:
    if user.role != Role.SUPER_ADMIN.value:
        raise SuperAdminRequired()
    return user


SuperAdmin = Annotated[User, Depends(require_super_admin)]


async def require_org_admin(user: CurrentUser) -> User:
    """기관 관리자 이상. 기관·마을·계정 관리 라우트가 쓴다."""
    if user.role not in authz.ORG_ADMIN_ROLES:
        raise OrgAdminRequired()
    return user


OrgAdmin = Annotated[User, Depends(require_org_admin)]


def check_confirm_password(user: User, header_value: str | None) -> None:
    """X-Confirm-Password(퍼센트 인코딩)가 지금 로그인한 계정의 비밀번호인가(문제점 83·84번).

    헤더는 latin-1 이라 화면이 encodeURIComponent 로 보내고 여기서 푼다. 연속 실패 제한은 두지
    않는다(요청 사항) — 이미 로그인한 사람의 마지막 확인이라서다.
    """
    if not header_value:
        raise ConfirmPasswordRequired()
    if not verify_password(unquote(header_value), user.password_hash):
        raise ConfirmPasswordWrong()


async def require_confirm_password(
    user: CurrentUser,
    x_confirm_password: Annotated[str | None, Header(alias="X-Confirm-Password")] = None,
) -> None:
    check_confirm_password(user, x_confirm_password)


#: 라우트에 붙이면 X-Confirm-Password 가 맞아야 실행된다.
PasswordConfirmed = Annotated[None, Depends(require_confirm_password)]


async def get_user_from_header_or_query(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
    access_token: str | None = None,
) -> User:
    """헤더 또는 ?access_token= 으로 인증한다.

    <audio src> · <img src> 같은 태그는 Authorization 헤더를 붙일 수 없어서
    쿼리로도 받아준다. 같은 JWT 라 권한 수준은 헤더와 동일하다.

    ⚠ 쿼리 토큰은 브라우저 히스토리와 프록시 로그에 남는다. 미리듣기처럼
      태그로 직접 물어야 하는 곳에만 쓰고, 일반 API 는 헤더만 받는다.
    """
    raw = credentials.credentials if credentials else access_token
    if not raw:
        raise Unauthorized()

    payload = decode_access_token(raw)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Unauthorized() from exc

    user = await db.get(User, user_id)
    if user is None:
        raise Unauthorized()
    _check_session(payload, user)
    return user


MediaUser = Annotated[User, Depends(get_user_from_header_or_query)]


def get_publisher(request: Request) -> MqttPublisher:
    """앱 수명주기(main.lifespan)에 붙여둔 MQTT 퍼블리셔를 꺼낸다."""
    return request.app.state.publisher


def get_live_registry(request: Request) -> LiveRegistry:
    """진행 중인 실시간 방송 세션 레지스트리.

    프로세스 메모리에 있다 — Icecast 연결과 WebSocket 은 DB 에 넣을 수 없다.
    """
    return request.app.state.live_registry


Db = Annotated[AsyncSession, Depends(get_db)]
Publisher = Annotated[MqttPublisher, Depends(get_publisher)]
LiveReg = Annotated[LiveRegistry, Depends(get_live_registry)]
