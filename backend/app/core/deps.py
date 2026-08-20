"""FastAPI 의존성 — 인증과 권한.

라우터가 쓰는 것은 이 3개뿐이다:

    CurrentUser      로그인한 계정 (없으면 401)
    Scope            담당 마을 범위 (조회 필터 · 대상 검사)
    SuperAdmin       super_admin 전용 라우트 가드
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import Role
from app.core.scope import VillageScope, scope_for
from app.core.security import decode_access_token
from app.db import get_db
from app.errors import SuperAdminRequired, Unauthorized
from app.live.registry import LiveRegistry
from app.models.org import User, UserVillage
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
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_village_scope(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> VillageScope:
    """계정의 담당 마을 범위를 만든다.

    super_admin 이면 user_villages 를 조회하지 않는다(전체 접근이므로 볼 필요가 없다).
    """
    if user.role == Role.SUPER_ADMIN.value:
        return VillageScope.for_super_admin()

    rows = await db.scalars(
        select(UserVillage.village_id).where(UserVillage.user_id == user.id)
    )
    return scope_for(user.role, rows.all())


Scope = Annotated[VillageScope, Depends(get_village_scope)]


async def require_super_admin(user: CurrentUser) -> User:
    if user.role != Role.SUPER_ADMIN.value:
        raise SuperAdminRequired()
    return user


SuperAdmin = Annotated[User, Depends(require_super_admin)]


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
