"""기관 · 마을 · 구역 · 계정 라우터.

권한 요약(관리자 계층 설계 2026-09-08, 2026-09-12 v2 §5):
  조회  기관/마을/구역 → 로그인 계정 전체. 범위(Scope)로 걸러진다.
  변경  기관          → 기관 관리자 이상, 관할(부분 트리) 안에서. 뿌리는 최고 관리자만
  변경  마을          → 기관 관리자 이상, 관할 안에서
  변경  구역          → 누구나, 자기 범위 안에서 (이장도 자기 마을 구역은 만든다)
  계정                → 기관 관리자 이상, 자기보다 아래 마디만
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.constants import ScheduleTarget
from app.core.deps import CurrentUser, Db, OrgAdmin, OrgIds, Publisher, Scope
from app.modules.device import service as device_service
from app.modules.org import service
from app.modules.schedule import service as schedule_service
from app.schemas.org import (
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
    TempPasswordOut,
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
    """내 관할 기관(평평한 목록). 지역 관리 트리·계정 화면의 드롭다운·스케줄 위자드가 쓴다."""
    return await service.list_organizations(db, org_ids)


@router.post(
    "/api/organizations", response_model=OrganizationOut, status_code=status.HTTP_201_CREATED
)
async def create_organization(
    payload: OrganizationCreate, db: Db, _: OrgAdmin, org_ids: OrgIds
) -> OrganizationOut:
    return await service.create_organization(db, payload, org_ids=org_ids)


@router.patch("/api/organizations/{org_id}", response_model=OrganizationOut)
async def update_organization(
    org_id: int, payload: OrganizationUpdate, db: Db, _: OrgAdmin, org_ids: OrgIds
) -> OrganizationOut:
    return await service.update_organization(db, org_id, payload, org_ids=org_ids)


@router.delete("/api/organizations/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(org_id: int, db: Db, _: OrgAdmin, org_ids: OrgIds) -> None:
    """빈 기관만. 자동방송이 이 기관을 대상으로 하면 SCHEDULE_TARGET_IN_USE."""
    await schedule_service.ensure_not_schedule_target(
        db, what="기관", target_scope=ScheduleTarget.ORGANIZATION.value, ids=[org_id]
    )
    await service.delete_organization(db, org_id, org_ids=org_ids)


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
    단말이 구독하는 topic 이 바뀌기 때문이다. 관리 기관 변경은 단말과 무관하다.

    순서는 ACL 먼저, 브로커가 적용한 것을 확인한 뒤 CONFIG — 반대로 하면 단말이 새 topic
    을 구독한 뒤 권한이 설치되기 전까지 나간 방송을 놓친다(2026-09-15 실측)."""
    before = await service.get_village(db, village_id, scope)
    out = await service.update_village(db, village_id, payload, scope, org_ids=org_ids)
    if out.village_token != before.village_token:
        await device_service.export_broker_accounts(db, wait_applied=True)
        await device_service.resync_config(db, publisher)
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

    자동방송이 이 마을이나 소속 단말을 직접 가리키면 막는다(SCHEDULE_TARGET_IN_USE) —
    지우면 대상 없는 규칙이 남아 매번 조용히 건너뛴다.
    """
    scope.ensure_allowed(village_id)
    macs = await service.macs_in_village(db, village_id)
    await schedule_service.ensure_not_schedule_target(
        db, what="마을", target_scope=ScheduleTarget.VILLAGE.value, ids=[village_id]
    )
    await schedule_service.ensure_not_schedule_target(
        db, what="마을 소속 단말", target_scope=ScheduleTarget.DEVICE.value, ids=macs
    )
    await service.delete_village(db, village_id)
    # 미배정으로 돌아간 단말들의 village topic 허용을 ACL 에서 먼저 빼고 CONFIG 를 보낸다.
    await device_service.export_broker_accounts(db)
    await device_service.clear_device_configs(publisher, macs, db)


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


@router.post("/api/users/{user_id}/temp-password", response_model=TempPasswordOut)
async def issue_temp_password(
    user_id: int, db: Db, actor: OrgAdmin, org_ids: OrgIds, scope: Scope
) -> TempPasswordOut:
    """임시 비밀번호 발급 — 응답에서 한 번만 보인다. 그 계정은 다음 로그인에서 바꿔야 한다."""
    password = await service.issue_temp_password(
        db, user_id, actor=actor, org_ids=org_ids, scope=scope
    )
    return TempPasswordOut(password=password)


@router.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int, db: Db, actor: OrgAdmin, org_ids: OrgIds, scope: Scope
) -> None:
    await service.delete_user(db, user_id, actor=actor, org_ids=org_ids, scope=scope)
