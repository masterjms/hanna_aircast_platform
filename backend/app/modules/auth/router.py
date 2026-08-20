"""인증 라우터.

토큰은 상태를 두지 않는다(JWT). 로그아웃은 클라이언트가 토큰을 버리는 것으로 끝나고,
서버는 204 만 돌려준다. 강제 무효화가 필요해지면 그때 블랙리스트를 추가한다.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import func, select

from app.constants import Role
from app.core.deps import CurrentUser, Db, Scope
from app.core.scope import VillageScope
from app.core.security import create_access_token, verify_password
from app.errors import InvalidCredentials
from app.models.device import Device
from app.models.org import User, UserVillage, Village
from app.schemas.auth import LoginRequest, LoginResponse, MeResponse, VillageBrief

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

    return MeResponse(
        id=user.id,
        username=user.username,
        role=Role(user.role),
        villages=villages,
        all_villages=scope.all_villages,
        device_count=int(device_count or 0),
    )


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, db: Db) -> LoginResponse:
    user = await db.scalar(select(User).where(User.username == payload.username))

    # 아이디가 없을 때와 비밀번호가 틀릴 때를 같은 에러로 돌려준다(계정 존재 여부 노출 방지).
    if user is None or not verify_password(payload.password, user.password_hash):
        raise InvalidCredentials()

    if user.role == Role.SUPER_ADMIN.value:
        scope = VillageScope.for_super_admin()
    else:
        rows = await db.scalars(
            select(UserVillage.village_id).where(UserVillage.user_id == user.id)
        )
        scope = VillageScope.for_villages(rows.all())

    token, expires_in = create_access_token(
        user_id=user.id, username=user.username, role=user.role
    )
    return LoginResponse(
        access_token=token,
        expires_in=expires_in,
        user=await _build_me(db, user, scope),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    return None


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser, db: Db, scope: Scope) -> MeResponse:
    return await _build_me(db, user, scope)
