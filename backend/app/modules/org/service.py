"""기관 · 마을 · 구역 · 계정 서비스.

이 모듈이 organizations / villages / zones / users / user_villages 테이블을 소유한다.
다른 모듈은 여기 함수를 통해서만 접근한다.

권한 규칙은 관리자 계층 설계(2026-09-08, 2026-09-12 v2) §5·§6 을 따른다. 요약:
  운영은 관할 전체에, 구조 변경은 출발지·도착지가 모두 내 관할일 때만, 계정은 나보다
  아래 마디만. "관할"은 조직 트리(organizations)의 부분 트리이지 주소가 아니다.

함수들이 받는 `org_ids` 는 app/core/authz.org_ids_under 의 결과다 — 이 계정이 다스리는
기관 id 집합(소속 기관 + 그 아래 전부). None 은 최고 관리자(전체)다.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import Role
from app.core import authz
from app.core.orgtree import load_tree
from app.core.presence import online_clause, online_cutoff
from app.core.scope import VillageScope
from app.core.security import hash_password
from app.core.village_token import next_village_code, token_for
from app.errors import (
    ApiError,
    DuplicateUsername,
    OrganizationInUse,
    OrganizationNotFound,
    OrganizationOutOfScope,
    TierTooLow,
    UserNotFound,
    VillageNotFound,
    ZoneNotFound,
)
from app.models.device import Device
from app.models.org import Organization, User, UserVillage, Village, Zone
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


# ── 기관 (관리자 계층 설계 §3 · v2 깊이 무제한) ─────────────────────────
async def _org_names(db: AsyncSession, ids: Iterable[int | None]) -> dict[int, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await db.execute(
        select(Organization.id, Organization.name).where(Organization.id.in_(wanted))
    )
    return dict(rows.all())


def _ensure_org_allowed(org_id: int | None, org_ids: set[int] | None) -> None:
    """기관이 내 관할인가. 최고 관리자(None)는 전부 허용. NULL 기관은 최고 관리자만."""
    if org_ids is None:
        return
    if org_id is None or org_id not in org_ids:
        raise OrganizationOutOfScope(detail={"organization_id": org_id})


def _ensure_parent_allowed(parent_id: int | None, org_ids: set[int] | None) -> None:
    """마디를 붙일(옮길) 자리가 내 관할인가. 뿌리(None)에 붙이는 것은 최고 관리자만."""
    if org_ids is None:
        return
    if parent_id is None or parent_id not in org_ids:
        raise OrganizationOutOfScope(detail={"parent_id": parent_id})


async def _load_org(db: AsyncSession, org_id: int) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None:
        raise OrganizationNotFound(detail={"organization_id": org_id})
    return org


async def list_organizations(db: AsyncSession, org_ids: set[int] | None) -> list[OrganizationOut]:
    """기관 목록 — 내 관할(부분 트리)만. 평평한 목록이고 트리는 화면이 parent_id 로 만든다.

    바로 아래 마을·계정·기관 수를 같이 세서 삭제 가능 여부를 화면이 안다. 질의는 기관
    수와 무관하게 5번이다.
    """
    stmt = select(Organization).order_by(Organization.name)
    if org_ids is not None:
        stmt = stmt.where(Organization.id.in_(org_ids))
    orgs = (await db.scalars(stmt)).all()
    if not orgs:
        return []

    ids = [o.id for o in orgs]
    village_counts = dict(
        (
            await db.execute(
                select(Village.organization_id, func.count())
                .where(Village.organization_id.in_(ids))
                .group_by(Village.organization_id)
            )
        ).all()
    )
    user_counts = dict(
        (
            await db.execute(
                select(User.organization_id, func.count())
                .where(User.organization_id.in_(ids))
                .group_by(User.organization_id)
            )
        ).all()
    )
    child_counts = dict(
        (
            await db.execute(
                select(Organization.parent_id, func.count())
                .where(Organization.parent_id.in_(ids))
                .group_by(Organization.parent_id)
            )
        ).all()
    )
    names = await _org_names(db, [o.parent_id for o in orgs])

    out = []
    for o in orgs:
        item = OrganizationOut.model_validate(o)
        item.parent_name = names.get(o.parent_id) if o.parent_id else None
        item.village_count = village_counts.get(o.id, 0)
        item.user_count = user_counts.get(o.id, 0)
        item.child_count = child_counts.get(o.id, 0)
        out.append(item)
    return out


async def create_organization(
    db: AsyncSession, payload: OrganizationCreate, *, org_ids: set[int] | None
) -> OrganizationOut:
    """마디 추가. 기관 관리자는 자기 관할 안의 마디 아래에만 붙인다(자기 마디 포함)."""
    _ensure_parent_allowed(payload.parent_id, org_ids)
    if payload.parent_id is not None:
        await _load_org(db, payload.parent_id)
    org = Organization(name=payload.name, parent_id=payload.parent_id)
    db.add(org)
    await db.flush()
    return (await list_organizations(db, {org.id}))[0]


async def update_organization(
    db: AsyncSession, org_id: int, payload: OrganizationUpdate, *, org_ids: set[int] | None
) -> OrganizationOut:
    """이름 바꾸기·옮기기.

    옮기기는 출발(지금 자리)·도착(새 부모)이 모두 관할이어야 한다(§6). 기관 관리자는
    결과적으로 자기 마디를 못 옮긴다 — 관할 안의 도착지는 전부 자기 아래라 순환이다.
    """
    org = await _load_org(db, org_id)
    _ensure_org_allowed(org_id, org_ids)
    data = payload.model_dump(exclude_unset=True)
    if "parent_id" in data and data["parent_id"] != org.parent_id:
        tree = await load_tree(db)
        if tree.would_cycle(org_id, data["parent_id"]):
            raise ApiError(
                "자기 자신이나 자기 아래 기관을 상위로 둘 수 없습니다.", code="ORG_CYCLE"
            )
        _ensure_parent_allowed(data["parent_id"], org_ids)
        if data["parent_id"] is not None:
            await _load_org(db, data["parent_id"])
    for key, value in data.items():
        setattr(org, key, value)
    await db.flush()
    return (await list_organizations(db, {org_id}))[0]


async def delete_organization(db: AsyncSession, org_id: int, *, org_ids: set[int] | None) -> None:
    """소속 마을·계정·하위 기관이 하나라도 있으면 지우지 않는다(설계 §3).

    빈 폴더만 지운다 — 안에 든 것이 있으면 사람이 먼저 옮기거나 지운다. 마을에는
    단말·이력·스케줄이 매달려 있어 연쇄 삭제는 두지 않는다.
    """
    org = await _load_org(db, org_id)
    _ensure_org_allowed(org_id, org_ids)
    in_use = await db.scalar(
        select(func.count()).select_from(Village).where(Village.organization_id == org_id)
    ) or await db.scalar(
        select(func.count()).select_from(User).where(User.organization_id == org_id)
    ) or await db.scalar(
        select(func.count()).select_from(Organization).where(Organization.parent_id == org_id)
    )
    if in_use:
        raise OrganizationInUse(detail={"organization_id": org_id})
    await db.delete(org)
    await db.flush()


# ── 마을 ─────────────────────────────────────────────────────────────────
async def _village_device_counts(
    db: AsyncSession, village_ids: Sequence[int]
) -> dict[int, tuple[int, int]]:
    """마을별 (등록 수, 온라인 수). 목록 화면이 N+1 질의를 하지 않도록 한 번에 센다.

    온라인 수를 같이 세는 이유: 방송은 온라인 단말에만 나간다. 등록 수만 보여주면
    운영자가 "3대에 나가겠구나" 하고 누르는데 실제로는 1대만 나가는 일이 생긴다.
    """
    if not village_ids:
        return {}
    online = online_clause(online_cutoff())
    rows = await db.execute(
        select(
            Device.village_id,
            func.count(),
            func.count().filter(online),
        )
        .where(Device.village_id.in_(village_ids))
        .group_by(Device.village_id)
    )
    return {vid: (total, online_n) for vid, total, online_n in rows.all()}


def _to_village_out(village: Village, counts: tuple[int, int]) -> VillageOut:
    out = VillageOut.model_validate(village)
    out.village_token = token_for(village.id, village.village_code)
    out.device_count, out.online_count = counts
    # 도형 자체는 싣지 않는다 — 있는지 여부만. 수십 KB 짜리가 목록 행마다 붙으면
    # 마을 관리 화면이 느려지고, 화면은 "경계가 들어왔나"만 알면 된다.
    out.has_boundary = village.boundary is not None
    return out


async def list_villages(db: AsyncSession, scope: VillageScope) -> list[VillageOut]:
    stmt = scope.apply(select(Village).order_by(Village.name), Village.id)
    villages = (await db.scalars(stmt)).all()
    counts = await _village_device_counts(db, [v.id for v in villages])
    names = await _org_names(db, [v.organization_id for v in villages])
    out = []
    for v in villages:
        item = _to_village_out(v, counts.get(v.id, (0, 0)))
        item.organization_name = names.get(v.organization_id) if v.organization_id else None
        out.append(item)
    return out


async def get_village(db: AsyncSession, village_id: int, scope: VillageScope) -> VillageOut:
    scope.ensure_allowed(village_id)
    village = await db.get(Village, village_id)
    if village is None:
        raise VillageNotFound()
    counts = await _village_device_counts(db, [village_id])
    out = _to_village_out(village, counts.get(village_id, (0, 0)))
    if village.organization_id:
        out.organization_name = (await _org_names(db, [village.organization_id])).get(
            village.organization_id
        )
    return out


async def _assign_village_code(db: AsyncSession, village: Village) -> bool:
    """b_code 가 있고 코드가 아직 없으면 12자리 코드를 만든다. 만들었으면 True.

    한 번 만든 코드는 b_code 가 바뀌어도 그대로 둔다(레지스트리 사양 §2 — 행정구역
    개편 때 바꾸면 그 마을 전 단말 재설정 + 과거 이력 단절).
    """
    if village.village_code is not None or not village.b_code:
        return False
    taken = await db.scalars(
        select(Village.village_code).where(Village.village_code.like(f"{village.b_code}%"))
    )
    village.village_code = next_village_code(village.b_code, [t for t in taken if t])
    await db.flush()
    return True


async def create_village(
    db: AsyncSession, payload: VillageCreate, *, actor: User, org_ids: set[int] | None
) -> VillageOut:
    """마을 추가. 붙일 마디는 내 관할 안이어야 한다(설계 §5).

    기관 관리자가 마디를 비우면 자기 기관에 붙는다. 기관 없는 마을(NULL)은 최고
    관리자만 만들 수 있다.
    """
    data = payload.model_dump()
    if actor.role == Role.ORG_ADMIN.value and data["organization_id"] is None:
        data["organization_id"] = actor.organization_id
    _ensure_org_allowed(data["organization_id"], org_ids)
    if data["organization_id"] is not None:
        await _load_org(db, data["organization_id"])

    village = Village(**data)
    db.add(village)
    await db.flush()
    await _assign_village_code(db, village)
    out = _to_village_out(village, (0, 0))
    if village.organization_id:
        out.organization_name = (await _org_names(db, [village.organization_id])).get(
            village.organization_id
        )
    return out


async def update_village(
    db: AsyncSession,
    village_id: int,
    payload: VillageUpdate,
    scope: VillageScope,
    *,
    org_ids: set[int] | None,
) -> VillageOut:
    scope.ensure_allowed(village_id)
    village = await db.get(Village, village_id)
    if village is None:
        raise VillageNotFound()
    data = payload.model_dump(exclude_unset=True)

    # 관리 기관 변경(설계 §6.2): 출발·도착 기관이 모두 관할이어야 한다. 관할 밖으로
    # 내보내거나 밖에서 데려오는 것은 두 관할을 다 가진 위 계층이 한다.
    if "organization_id" in data and data["organization_id"] != village.organization_id:
        _ensure_org_allowed(village.organization_id, org_ids)
        _ensure_org_allowed(data["organization_id"], org_ids)
        if data["organization_id"] is not None:
            await _load_org(db, data["organization_id"])

    for key, value in data.items():
        setattr(village, key, value)
    await db.flush()
    # 주소가 처음 들어오면 그때 12자리 코드가 생긴다(그 전엔 legacy 8자리).
    await _assign_village_code(db, village)
    return await get_village(db, village_id, scope)


async def delete_village(db: AsyncSession, village_id: int) -> None:
    """마을 삭제.

    zones 는 CASCADE 로 함께 지워지고, devices.village_id 는 SET NULL 로 미배정이 된다.
    단말이 사라지지는 않는다 — 물건은 현장에 그대로 있으니까.

    ⚠ 미배정으로 돌아간 단말에는 CONFIG retain 을 지워줘야 한다.
      라우터가 삭제 전 소속 MAC 을 모아 device 모듈에 넘긴다.
    """
    village = await db.get(Village, village_id)
    if village is None:
        raise VillageNotFound()
    await db.delete(village)
    await db.flush()


async def macs_in_village(db: AsyncSession, village_id: int) -> list[str]:
    """마을 소속 단말 MAC. 삭제 전 CONFIG 정리 대상을 뽑을 때 쓴다."""
    rows = await db.scalars(select(Device.mac).where(Device.village_id == village_id))
    return list(rows.all())


# ── 구역 ─────────────────────────────────────────────────────────────────
async def list_zones(db: AsyncSession, village_id: int, scope: VillageScope) -> list[ZoneOut]:
    scope.ensure_allowed(village_id)
    zones = (
        await db.scalars(
            select(Zone).where(Zone.village_id == village_id).order_by(Zone.name)
        )
    ).all()

    counts: dict[int, tuple[int, int]] = {}
    if zones:
        online = online_clause(online_cutoff())
        rows = await db.execute(
            select(Device.zone_id, func.count(), func.count().filter(online))
            .where(Device.zone_id.in_([z.id for z in zones]))
            .group_by(Device.zone_id)
        )
        counts = {zid: (total, online_n) for zid, total, online_n in rows.all()}

    result = []
    for zone in zones:
        out = ZoneOut.model_validate(zone)
        out.device_count, out.online_count = counts.get(zone.id, (0, 0))
        result.append(out)
    return result


async def create_zone(
    db: AsyncSession, village_id: int, payload: ZoneCreate, scope: VillageScope
) -> ZoneOut:
    scope.ensure_allowed(village_id)
    if await db.get(Village, village_id) is None:
        raise VillageNotFound()
    zone = Zone(village_id=village_id, **payload.model_dump())
    db.add(zone)
    await db.flush()
    return ZoneOut.model_validate(zone)


async def _load_zone(db: AsyncSession, zone_id: int, scope: VillageScope) -> Zone:
    zone = await db.get(Zone, zone_id)
    if zone is None:
        raise ZoneNotFound()
    scope.ensure_allowed(zone.village_id)
    return zone


async def update_zone(
    db: AsyncSession, zone_id: int, payload: ZoneUpdate, scope: VillageScope
) -> ZoneOut:
    zone = await _load_zone(db, zone_id, scope)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(zone, key, value)
    await db.flush()
    return ZoneOut.model_validate(zone)


async def delete_zone(db: AsyncSession, zone_id: int, scope: VillageScope) -> None:
    """구역만 지운다. 소속 단말은 devices.zone_id 가 SET NULL 되어 마을에는 남는다."""
    zone = await _load_zone(db, zone_id, scope)
    await db.delete(zone)
    await db.flush()


async def zone_village_id(db: AsyncSession, zone_id: int) -> int:
    """구역이 속한 마을. device 모듈이 배정 검증할 때 쓴다."""
    village_id = await db.scalar(select(Zone.village_id).where(Zone.id == zone_id))
    if village_id is None:
        raise ZoneNotFound()
    return village_id


async def village_exists(db: AsyncSession, village_id: int) -> bool:
    return await db.scalar(select(Village.id).where(Village.id == village_id)) is not None


# ── 계정 ─────────────────────────────────────────────────────────────────
async def villages_of_user(db: AsyncSession, user_id: int) -> list[int]:
    rows = await db.scalars(select(UserVillage.village_id).where(UserVillage.user_id == user_id))
    return sorted(rows.all())


async def _set_user_villages(db: AsyncSession, user_id: int, village_ids: Iterable[int]) -> None:
    """담당 마을 전체 교체. 부분 수정보다 단순하고, 화면도 전체를 보내온다."""
    wanted = sorted(set(village_ids))
    for village_id in wanted:
        if not await village_exists(db, village_id):
            raise VillageNotFound(detail={"village_id": village_id})

    await db.execute(delete(UserVillage).where(UserVillage.user_id == user_id))
    for village_id in wanted:
        db.add(UserVillage(user_id=user_id, village_id=village_id))
    await db.flush()


async def villages_by_user(db: AsyncSession, user_ids: Sequence[int]) -> dict[int, list[int]]:
    """계정별 담당 마을. 목록 화면이 건마다 읽지 않도록 한 번에 가져온다.

    예전에는 계정 하나마다 두 번 읽었다 — 관리할 수 있는지 판정에 한 번,
    응답을 만들 때 또 한 번. 계정 50개에 질의가 51번 나갔다(2026-09-10 실측).
    """
    if not user_ids:
        return {}
    out: dict[int, list[int]] = {}
    for uid, vid in (
        await db.execute(
            select(UserVillage.user_id, UserVillage.village_id).where(
                UserVillage.user_id.in_(set(user_ids))
            )
        )
    ).all():
        out.setdefault(uid, []).append(vid)
    return out


async def _to_user_out(
    db: AsyncSession,
    user: User,
    names: dict[int, str] | None = None,
    villages: dict[int, list[int]] | None = None,
) -> UserOut:
    out = UserOut.model_validate(user)
    # 배치로 받은 게 있으면 쓰고, 단건 경로(생성·수정)는 지금처럼 직접 읽는다.
    out.village_ids = (
        sorted(villages.get(user.id, [])) if villages is not None
        else await villages_of_user(db, user.id)
    )
    if user.organization_id:
        if names is None:
            names = await _org_names(db, [user.organization_id])
        out.organization_name = names.get(user.organization_id)
    return out


async def _manageable(
    db: AsyncSession,
    target: User,
    actor: User,
    org_ids: set[int] | None,
    scope: VillageScope,
    villages: dict[int, list[int]] | None = None,
) -> bool:
    """actor 가 target 계정을 만지고 볼 수 있는가(설계 §5).

    나보다 아래여야 하고, 그 계정의 범위가 내 관할 안이어야 한다 — 기관 계정은
    기관으로, 이장 계정은 담당 마을 전부로 본다. 기관 관리자끼리는 트리 위치가 위아래를
    정한다: 내 **아래** 마디의 기관 관리자만 내 것이고, 같은 마디는 동료라 서로 못 만진다.
    """
    if actor.role == Role.SUPER_ADMIN.value:
        return True
    if target.role == Role.SUPER_ADMIN.value:
        return False
    if target.role in authz.ORG_ROLES:
        return (
            org_ids is not None
            and target.organization_id in org_ids
            and target.organization_id != actor.organization_id
        )
    # village_admin — 담당 마을이 하나라도 관할 밖이면 내 계정이 아니다.
    if villages is not None:
        mine = villages.get(target.id, [])
    else:
        mine = await villages_of_user(db, target.id)
    return all(scope.allows(v) for v in mine)


async def list_users(
    db: AsyncSession, *, actor: User, org_ids: set[int] | None, scope: VillageScope
) -> list[UserOut]:
    users = (await db.scalars(select(User).order_by(User.username))).all()
    names = await _org_names(db, [u.organization_id for u in users])
    villages = await villages_by_user(db, [u.id for u in users])
    out = []
    for u in users:
        if await _manageable(db, u, actor, org_ids, scope, villages):
            out.append(await _to_user_out(db, u, names, villages))
    return out


def _expiry_from(valid_days: int | None) -> dt.datetime | None:
    """오늘부터 N일. None 이면 무기한.

    "7일" 은 7일째까지 쓸 수 있고 8일째부터 정리 대상이라는 뜻이다(문제점 26번).
    """
    if valid_days is None:
        return None
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=valid_days)


async def _check_role_shape(
    db: AsyncSession,
    *,
    role: str,
    organization_id: int | None,
    village_ids: Iterable[int] | None,
    actor: User,
    org_ids: set[int] | None,
    scope: VillageScope,
) -> None:
    """역할에 맞는 소속만 허용하고, 그 소속이 내 관할인지 본다."""
    if role not in authz.manageable_roles(actor.role):
        raise TierTooLow(detail={"role": role})

    if role in authz.ORG_ROLES:
        if organization_id is None:
            raise ApiError("기관 관리자에게는 소속 기관이 필요합니다.", code="ORG_REQUIRED")
        await _load_org(db, organization_id)
        _ensure_org_allowed(organization_id, org_ids)
        # 같은 마디의 기관 관리자는 동료다 — 만들면 내 관할이 옆으로 샌다. 아래 마디만.
        if actor.role == Role.ORG_ADMIN.value and organization_id == actor.organization_id:
            raise TierTooLow(detail={"organization_id": organization_id})
        if village_ids:
            raise ApiError(
                "기관 관리자에게는 담당 마을을 지정하지 않습니다. 기관으로 범위가 정해집니다.",
                code="ORG_ADMIN_HAS_NO_VILLAGES",
            )
        return

    if organization_id is not None:
        raise ApiError(
            "이 역할에는 소속 기관을 두지 않습니다.", code="ROLE_HAS_NO_ORG", detail={"role": role}
        )
    if role == Role.VILLAGE_ADMIN.value:
        for vid in village_ids or []:
            scope.ensure_allowed(vid)
    elif village_ids:
        raise ApiError(
            "최고 관리자에게는 담당 마을을 지정하지 않습니다.", code="SUPER_ADMIN_HAS_NO_VILLAGES"
        )


async def create_user(
    db: AsyncSession,
    payload: UserCreate,
    *,
    actor: User,
    org_ids: set[int] | None,
    scope: VillageScope,
) -> UserOut:
    existing = await db.scalar(select(User.id).where(User.username == payload.username))
    if existing is not None:
        raise DuplicateUsername()

    role = payload.role.value
    await _check_role_shape(
        db,
        role=role,
        organization_id=payload.organization_id,
        village_ids=payload.village_ids,
        actor=actor,
        org_ids=org_ids,
        scope=scope,
    )

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=role,
        organization_id=payload.organization_id if role in authz.ORG_ROLES else None,
        expires_at=_expiry_from(payload.valid_days),
    )
    db.add(user)
    await db.flush()

    if role == Role.VILLAGE_ADMIN.value:
        await _set_user_villages(db, user.id, payload.village_ids)
    return await _to_user_out(db, user)


async def update_user(
    db: AsyncSession,
    user_id: int,
    payload: UserUpdate,
    *,
    actor: User,
    org_ids: set[int] | None,
    scope: VillageScope,
) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFound()
    if not await _manageable(db, user, actor, org_ids, scope):
        raise TierTooLow(detail={"user_id": user_id})

    data = payload.model_dump(exclude_unset=True)
    if "password" in data and data["password"]:
        user.password_hash = hash_password(data["password"])
    # 보낸 경우에만 만료일을 다시 센다. 값이 null 이면 무기한으로 바꾼다.
    if "valid_days" in data:
        user.expires_at = _expiry_from(data["valid_days"])

    # 역할·기관·담당 마을은 한 덩어리로 검사한다 — 바꾼 뒤의 모양이 맞아야 한다.
    new_role = Role(data["role"]).value if data.get("role") is not None else user.role
    new_org = data.get("organization_id", user.organization_id)
    if new_role not in authz.ORG_ROLES and "organization_id" not in data:
        new_org = None  # 역할이 기관형이 아니게 되면 소속은 자동으로 비운다
    if data.get("village_ids") is not None:
        new_villages: list[int] | None = data["village_ids"]
    elif new_role == Role.VILLAGE_ADMIN.value:
        new_villages = await villages_of_user(db, user_id)
    else:
        new_villages = None

    if {"role", "organization_id", "village_ids"} & set(data):
        await _check_role_shape(
            db,
            role=new_role,
            organization_id=new_org,
            village_ids=new_villages,
            actor=actor,
            org_ids=org_ids,
            scope=scope,
        )
        user.role = new_role
        user.organization_id = new_org
        if new_role == Role.VILLAGE_ADMIN.value:
            await _set_user_villages(db, user_id, new_villages or [])
        else:
            await db.execute(delete(UserVillage).where(UserVillage.user_id == user_id))

    await db.flush()
    return await _to_user_out(db, user)


async def delete_user(
    db: AsyncSession,
    user_id: int,
    *,
    actor: User,
    org_ids: set[int] | None,
    scope: VillageScope,
) -> None:
    if user_id == actor.id:
        raise ApiError("자기 계정은 삭제할 수 없습니다.", code="CANNOT_DELETE_SELF")

    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFound()
    if not await _manageable(db, user, actor, org_ids, scope):
        raise TierTooLow(detail={"user_id": user_id})

    # 마지막 super_admin 을 지우면 아무도 시스템을 관리할 수 없게 된다.
    if user.role == Role.SUPER_ADMIN.value:
        remaining = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == Role.SUPER_ADMIN.value, User.id != user_id)
        )
        if not remaining:
            raise ApiError(
                "마지막 최고 관리자 계정은 삭제할 수 없습니다.",
                code="LAST_SUPER_ADMIN",
            )

    await db.delete(user)
    await db.flush()
