"""기관 · 마을 · 구역 · 계정 라우터.

권한 요약(관리자 계층 설계 2026-09-08 §5):
  조회  기관/마을/구역 → 로그인 계정 전체. 범위(Scope)로 걸러진다.
  변경  기관          → 최고 관리자만
  변경  마을          → 시·군 이상, 관할 안에서
  변경  구역          → 누구나, 자기 범위 안에서 (이장도 자기 마을 구역은 만든다)
  계정                → 시·군 이상, 자기보다 낮은 계층만
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.deps import CurrentUser, Db, OrgAdmin, OrgIds, Publisher, Scope, SuperAdmin
from app.modules.device import service as device_service
from app.modules.org import service
from app.schemas.org import (
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
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


# ── 기관 ─────────────────────────────────────────────────────────────────
@router.get("/api/organizations", response_model=list[OrganizationOut])
async def list_organizations(db: Db, _: CurrentUser, org_ids: OrgIds) -> list[OrganizationOut]:
    """내 관할 기관. 마을·계정 화면의 드롭다운이 쓴다."""
    return await service.list_organizations(db, org_ids)


@router.post(
    "/api/organizations", response_model=OrganizationOut, status_code=status.HTTP_201_CREATED
)
async def create_organization(
    payload: OrganizationCreate, db: Db, _: SuperAdmin
) -> OrganizationOut:
    return await service.create_organization(db, payload)


@router.patch("/api/organizations/{org_id}", response_model=OrganizationOut)
async def update_organization(
    org_id: int, payload: OrganizationUpdate, db: Db, _: SuperAdmin
) -> OrganizationOut:
    return await service.update_organization(db, org_id, payload)


@router.delete("/api/organizations/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(org_id: int, db: Db, _: SuperAdmin) -> None:
    await service.delete_organization(db, org_id)


@router.get("/api/organizations/suggest")
async def suggest_organization(b_code: str, db: Db, _: CurrentUser) -> dict[str, int | None]:
    """주소(법정동코드)로 관리 기관을 제안한다. 제안일 뿐 권한과 무관하다(설계 §1)."""
    return {"organization_id": await service.suggest_organization(db, b_code)}


# ── 마을 ─────────────────────────────────────────────────────────────────
@router.get("/api/villages", response_model=list[VillageOut])
async def list_villages(db: Db, scope: Scope) -> list[VillageOut]:
    return await service.list_villages(db, scope)


@router.get("/api/villages/{village_id}", response_model=VillageOut)
async def get_village(village_id: int, db: Db, scope: Scope) -> VillageOut:
    return await service.get_village(db, village_id, scope)


@router.post("/api/villages", response_model=VillageOut, status_code=status.HTTP_201_CREATED)
async def create_village(
    payload: VillageCreate, db: Db, actor: OrgAdmin, org_ids: OrgIds
) -> VillageOut:
    return await service.create_village(db, payload, actor=actor, org_ids=org_ids)


@router.patch("/api/villages/{village_id}", response_model=VillageOut)
async def update_village(
    village_id: int,
    payload: VillageUpdate,
    db: Db,
    scope: Scope,
    _: OrgAdmin,
    org_ids: OrgIds,
    publisher: Publisher,
) -> VillageOut:
    """마을 수정. 주소를 처음 넣어 MQTT 문자열이 legacy → 12자리로 바뀌면
    그 마을 단말들의 CONFIG 를 새 버전으로 다시 내리고 ACL 도 다시 만든다 —
    단말이 구독하는 topic 이 바뀌기 때문이다. 관리 기관 변경은 단말과 무관하다."""
    before = await service.get_village(db, village_id, scope)
    out = await service.update_village(db, village_id, payload, scope, org_ids=org_ids)
    if out.village_token != before.village_token:
        await device_service.resync_config(db, publisher)
        await device_service.export_broker_accounts(db)
    return out


@router.delete("/api/villages/{village_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_village(
    village_id: int,
    db: Db,
    scope: Scope,
    _: OrgAdmin,
    publisher: Publisher,
) -> None:
    """마을 삭제. 소속 단말은 미배정으로 남는다.

    삭제 전에 MAC 을 모아두고, 삭제 후 CONFIG retain 을 지운다.
    안 지우면 단말이 재접속할 때 브로커가 없어진 마을 배정을 다시 물려준다.
    """
    scope.ensure_allowed(village_id)
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
async def create_zone(village_id: int, payload: ZoneCreate, db: Db, scope: Scope) -> ZoneOut:
    return await service.create_zone(db, village_id, payload, scope)


@router.patch("/api/zones/{zone_id}", response_model=ZoneOut)
async def update_zone(zone_id: int, payload: ZoneUpdate, db: Db, scope: Scope) -> ZoneOut:
    return await service.update_zone(db, zone_id, payload, scope)


@router.delete("/api/zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone(zone_id: int, db: Db, scope: Scope) -> None:
    await service.delete_zone(db, zone_id, scope)


# ── 계정 ─────────────────────────────────────────────────────────────────
@router.get("/api/users", response_model=list[UserOut])
async def list_users(db: Db, actor: OrgAdmin, org_ids: OrgIds, scope: Scope) -> list[UserOut]:
    """내가 관리할 수 있는 계정만 — 나보다 낮은 계층이고 범위가 내 관할 안인 것."""
    return await service.list_users(db, actor=actor, org_ids=org_ids, scope=scope)


@router.post("/api/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate, db: Db, actor: OrgAdmin, org_ids: OrgIds, scope: Scope
) -> UserOut:
    return await service.create_user(db, payload, actor=actor, org_ids=org_ids, scope=scope)


@router.patch("/api/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int, payload: UserUpdate, db: Db, actor: OrgAdmin, org_ids: OrgIds, scope: Scope
) -> UserOut:
    return await service.update_user(
        db, user_id, payload, actor=actor, org_ids=org_ids, scope=scope
    )


@router.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int, db: Db, actor: OrgAdmin, org_ids: OrgIds, scope: Scope
) -> None:
    await service.delete_user(db, user_id, actor=actor, org_ids=org_ids, scope=scope)
