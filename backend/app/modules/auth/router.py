"""인증 라우터.

토큰은 상태를 두지 않는다(JWT). 로그아웃은 클라이언트가 토큰을 버리는 것으로 끝나고,
서버는 204 만 돌려준다. 강제 무효화가 필요해지면 그때 블랙리스트를 추가한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select

from app.core import authz
from app.core.deps import CurrentUser, Db, Scope
from app.core.scope import VillageScope
from app.core.security import create_access_token, verify_password
from app.errors import AccountExpired, InvalidCredentials
from app.models.auth_log import LoginEvent
from app.models.device import Device
from app.models.org import Organization, User, Village
from app.modules.org import service as org_service
from app.schemas.auth import (
    LoginEventOut,
    LoginEventPage,
    LoginRequest,
    LoginResponse,
    MeResponse,
    PasswordChangeRequest,
    VillageBrief,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _build_me(db: Db, user: User, scope: VillageScope) -> MeResponse:
    """상단바의 '담당 범위 · 전체 12개 마을 · 300대' 표시에 필요한 것들."""
    stmt = select(Village.id, Village.name).order_by(Village.name)
    if not scope.all_villages:
        stmt = stmt.where(Village.id.in_(scope.village_ids))
    villages = [VillageBrief(id=vid, name=name) for vid, name in (await db.execute(stmt)).all()]

    count_stmt = select(func.count()).select_from(Device)
    if scope.all_villages:
        # 미배정 단말도 super_admin 의 관리 대상이므로 함께 센다.
        device_count = await db.scalar(count_stmt)
    else:
        device_count = await db.scalar(
            count_stmt.where(Device.village_id.in_(scope.village_ids))
        )

    org_name = None
    if user.organization_id is not None:
        org_name = await db.scalar(
            select(Organization.name).where(Organization.id == user.organization_id)
        )

    return MeResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        villages=villages,
        all_villages=scope.all_villages,
        device_count=int(device_count or 0),
        organization_id=user.organization_id,
        organization_name=org_name,
        must_change_password=user.must_change_password,
    )


def _client_ip(request: Request) -> str | None:
    """nginx 뒤라 X-Real-IP(또는 X-Forwarded-For 첫 값)가 실제 접속 주소다."""
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()[:45]
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:45]
    return request.client.host[:45] if request.client else None


async def _record_login(
    db: Db, request: Request, *, username: str, user: User | None, result: str
) -> None:
    """로그인 시도 한 줄(문제점 65번). 실패도 남긴다.

    예외로 응답하면 미들웨어가 롤백하므로 여기서 먼저 커밋한다.
    """
    db.add(
        LoginEvent(
            user_id=user.id if user else None,
            username=username[:50],
            ip=_client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:255] or None,
            result=result,
        )
    )
    await db.commit()


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, db: Db, request: Request) -> LoginResponse:
    user = await db.scalar(select(User).where(User.username == payload.username))

    # 아이디가 없을 때와 비밀번호가 틀릴 때를 같은 에러로 돌려준다(계정 존재 여부 노출 방지).
    if user is None:
        await _record_login(
            db, request, username=payload.username, user=None, result="unknown_user"
        )
        raise InvalidCredentials()
    if not verify_password(payload.password, user.password_hash):
        await _record_login(
            db, request, username=payload.username, user=user, result="bad_password"
        )
        raise InvalidCredentials()

    # 정리 작업은 주기적으로 돈다 — 그 사이에 만료된 계정으로 들어오는 것을 막는다.
    if user.is_expired():
        await _record_login(db, request, username=payload.username, user=user, result="expired")
        raise AccountExpired()

    await _record_login(db, request, username=payload.username, user=user, result="ok")

    scope = await authz.resolve_scope(db, user)

    token, expires_in = create_access_token(
        user_id=user.id, username=user.username, role=user.role
    )
    return LoginResponse(
        access_token=token,
        expires_in=expires_in,
        user=await _build_me(db, user, scope),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: CurrentUser, db: Db) -> None:
    """「로그아웃」을 눌렀을 때만 온다. 그 계정의 가장 최근 열린 로그인 줄에 해지 시각을 적는다."""
    last = await db.scalar(
        select(LoginEvent)
        .where(
            LoginEvent.user_id == user.id,
            LoginEvent.result == "ok",
            LoginEvent.logged_out_at.is_(None),
        )
        .order_by(LoginEvent.logged_in_at.desc())
        .limit(1)
    )
    if last is not None:
        last.logged_out_at = func.now()
    return None


@router.get("/logins", response_model=LoginEventPage)
async def login_history(
    db: Db,
    actor: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    size: int = 10,
    q: Annotated[str | None, Query(max_length=60)] = None,
) -> LoginEventPage:
    """로그인 기록(문제점 65번). 최고 관리자는 전부, 그 외는 자기 기록만. size 10·20·50."""
    if size not in (10, 20, 50):
        size = 10
    stmt = select(LoginEvent)
    if actor.role != "super_admin":
        stmt = stmt.where(LoginEvent.user_id == actor.id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(LoginEvent.username.ilike(like) | LoginEvent.ip.ilike(like))
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        await db.scalars(
            stmt.order_by(LoginEvent.logged_in_at.desc()).offset((page - 1) * size).limit(size)
        )
    ).all()
    return LoginEventPage(
        total=total, page=page, size=size, items=[LoginEventOut.model_validate(r) for r in rows]
    )


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser, db: Db, scope: Scope) -> MeResponse:
    return await _build_me(db, user, scope)


@router.post("/password", response_model=MeResponse)
async def change_password(
    payload: PasswordChangeRequest, user: CurrentUser, db: Db
) -> MeResponse:
    """자기 비밀번호 변경. 임시 비밀번호 계정은 여기서만 풀린다(향후검토 10번)."""
    await org_service.change_own_password(
        db,
        user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return await _build_me(db, user, await authz.resolve_scope(db, user))
