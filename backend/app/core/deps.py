"""FastAPI 의존성 — 인증과 권한.

라우터가 쓰는 것:

    CurrentUser      로그인한 계정 (없으면 401)
    Scope            담당 마을 범위 (조회 필터 · 대상 검사)
    OrgIds           이 계정이 다스리는 기관 id 집합 (최고 관리자는 None=전체)
    OrgAdmin         시·군 관리자 이상 가드 (마을·계정 관리)
    SuperAdmin       super_admin 전용 라우트 가드

범위 산출 규칙은 app/core/authz.py — 관리자 계층 설계(2026-09-08) §4.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import Role
from app.core import authz
from app.core.scope import VillageScope
from app.core.security import decode_access_token
from app.db import get_db
from app.errors import OrgAdminRequired, SuperAdminRequired, Unauthorized
from app.live.registry import LiveRegistry
from app.models.org import User
from app.mqtt.publisher import MqttPublisher

# auto_error=False 로 두고 401 을 우리 에러 규약으로 직접 던진다.
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
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
    # 토큰 유효기간이 계정 만료보다 길 수 있다. 이미 발급된 토큰도 막는다.
    if user.is_expired():
        raise Unauthorized()
    return user


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
    """시·군 관리자 이상. 마을·구역(관할)·계정 관리 라우트가 쓴다."""
    if user.role not in authz.ORG_ADMIN_ROLES:
        raise OrgAdminRequired()
    return user


OrgAdmin = Annotated[User, Depends(require_org_admin)]


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
