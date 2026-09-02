"""마을 · 구역 · 계정 라우터.

권한 요약:
  조회  마을/구역  → 로그인 계정 전체. 단, village_admin 은 담당 마을만 보인다.
  변경  마을/구역  → super_admin 전용 (화면 설계상 '마을 관리'가 super_admin 메뉴)
  계정             → super_admin 전용
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.deps import Db, Publisher, Scope, SuperAdmin
from app.modules.device import service as device_service
from app.modules.org import service
from app.schemas.org import (
    UserCreate,
    UserOut,
    UserUpdate,
    VillageCreate,
    VillageOut,
    VillageUpdate,
    ZoneCreate,
    ZoneOut,
    ZoneUpdate,
)

router = APIRouter(tags=["org"])


# ── 마을 ─────────────────────────────────────────────────────────────────
@router.get("/api/villages", response_model=list[VillageOut])
async def list_villages(db: Db, scope: Scope) -> list[VillageOut]:
    return await service.list_villages(db, scope)


@router.get("/api/villages/{village_id}", response_model=VillageOut)
async def get_village(village_id: int, db: Db, scope: Scope) -> VillageOut:
    return await service.get_village(db, village_id, scope)


@router.post("/api/villages", response_model=VillageOut, status_code=status.HTTP_201_CREATED)
async def create_village(payload: VillageCreate, db: Db, _: SuperAdmin) -> VillageOut:
    return await service.create_village(db, payload)


@router.patch("/api/villages/{village_id}", response_model=VillageOut)
async def update_village(
    village_id: int, payload: VillageUpdate, db: Db, scope: Scope, _: SuperAdmin
) -> VillageOut:
    return await service.update_village(db, village_id, payload, scope)


@router.delete("/api/villages/{village_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_village(
    village_id: int,
    db: Db,
    _: SuperAdmin,
    publisher: Publisher,
) -> None:
    """마을 삭제. 소속 단말은 미배정으로 남는다.

    삭제 전에 MAC 을 모아두고, 삭제 후 CONFIG retain 을 지운다.
    안 지우면 단말이 재접속할 때 브로커가 없어진 마을 배정을 다시 물려준다.
    """
    macs = await service.macs_in_village(db, village_id)
    await service.delete_village(db, village_id)
    await device_service.clear_device_configs(publisher, macs, db)
    # 미배정으로 돌아간 단말들의 village topic 허용도 ACL 에서 빠져야 한다.
    await device_service.export_broker_accounts(db)


# ── 구역 ─────────────────────────────────────────────────────────────────
@router.get("/api/villages/{village_id}/zones", response_model=list[ZoneOut])
async def list_zones(village_id: int, db: Db, scope: Scope) -> list[ZoneOut]:
    return await service.list_zones(db, village_id, scope)


@router.post(
    "/api/villages/{village_id}/zones",
    response_model=ZoneOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_zone(
    village_id: int, payload: ZoneCreate, db: Db, scope: Scope, _: SuperAdmin
) -> ZoneOut:
    return await service.create_zone(db, village_id, payload, scope)


@router.patch("/api/zones/{zone_id}", response_model=ZoneOut)
async def update_zone(
    zone_id: int, payload: ZoneUpdate, db: Db, scope: Scope, _: SuperAdmin
) -> ZoneOut:
    return await service.update_zone(db, zone_id, payload, scope)


@router.delete("/api/zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone(zone_id: int, db: Db, scope: Scope, _: SuperAdmin) -> None:
    await service.delete_zone(db, zone_id, scope)


# ── 계정 ─────────────────────────────────────────────────────────────────
@router.get("/api/users", response_model=list[UserOut])
async def list_users(db: Db, _: SuperAdmin) -> list[UserOut]:
    return await service.list_users(db)


@router.post("/api/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, db: Db, _: SuperAdmin) -> UserOut:
    return await service.create_user(db, payload)


@router.patch("/api/users/{user_id}", response_model=UserOut)
async def update_user(user_id: int, payload: UserUpdate, db: Db, _: SuperAdmin) -> UserOut:
    return await service.update_user(db, user_id, payload)


@router.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, db: Db, actor: SuperAdmin) -> None:
    await service.delete_user(db, user_id, acting_user_id=actor.id)
