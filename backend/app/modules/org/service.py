"""마을 · 구역 · 계정 서비스.

이 모듈이 villages / zones / users / user_villages 테이블을 소유한다.
다른 모듈은 여기 함수를 통해서만 접근한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import Role
from app.core.presence import online_clause, online_cutoff
from app.core.scope import VillageScope
from app.core.security import hash_password
from app.core.village_token import next_village_code, token_for
from app.errors import (
    ApiError,
    DuplicateUsername,
    UserNotFound,
    VillageNotFound,
    ZoneNotFound,
)
from app.models.device import Device
from app.models.org import User, UserVillage, Village, Zone
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
    return [_to_village_out(v, counts.get(v.id, (0, 0))) for v in villages]


async def get_village(db: AsyncSession, village_id: int, scope: VillageScope) -> VillageOut:
    scope.ensure_allowed(village_id)
    village = await db.get(Village, village_id)
    if village is None:
        raise VillageNotFound()
    counts = await _village_device_counts(db, [village_id])
    return _to_village_out(village, counts.get(village_id, (0, 0)))


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


async def create_village(db: AsyncSession, payload: VillageCreate) -> VillageOut:
    village = Village(**payload.model_dump())
    db.add(village)
    await db.flush()
    await _assign_village_code(db, village)
    return _to_village_out(village, (0, 0))


async def update_village(
    db: AsyncSession, village_id: int, payload: VillageUpdate, scope: VillageScope
) -> VillageOut:
    scope.ensure_allowed(village_id)
    village = await db.get(Village, village_id)
    if village is None:
        raise VillageNotFound()
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(village, key, value)
    await db.flush()
    # 주소가 처음 들어오면 그때 12자리 코드가 생긴다(그 전엔 legacy 8자리).
    await _assign_village_code(db, village)
    counts = await _village_device_counts(db, [village_id])
    return _to_village_out(village, counts.get(village_id, (0, 0)))


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


async def _to_user_out(db: AsyncSession, user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.village_ids = await villages_of_user(db, user.id)
    return out


async def list_users(db: AsyncSession) -> list[UserOut]:
    users = (await db.scalars(select(User).order_by(User.username))).all()
    return [await _to_user_out(db, u) for u in users]


async def create_user(db: AsyncSession, payload: UserCreate) -> UserOut:
    existing = await db.scalar(select(User.id).where(User.username == payload.username))
    if existing is not None:
        raise DuplicateUsername()

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role.value,
    )
    db.add(user)
    await db.flush()

    # super_admin 은 전체 접근이라 담당 마을 목록을 두지 않는다 — 있으면 오해만 산다.
    if payload.role is Role.VILLAGE_ADMIN:
        await _set_user_villages(db, user.id, payload.village_ids)
    return await _to_user_out(db, user)


async def update_user(db: AsyncSession, user_id: int, payload: UserUpdate) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFound()

    data = payload.model_dump(exclude_unset=True)
    if "password" in data and data["password"]:
        user.password_hash = hash_password(data["password"])
    if "role" in data and data["role"] is not None:
        user.role = Role(data["role"]).value
        if user.role == Role.SUPER_ADMIN.value:
            # 전체 접근으로 승격되면 담당 마을 목록은 의미가 없어진다.
            await db.execute(delete(UserVillage).where(UserVillage.user_id == user_id))

    if data.get("village_ids") is not None:
        if user.role == Role.SUPER_ADMIN.value:
            raise ApiError(
                "최고 관리자에게는 담당 마을을 지정하지 않습니다.",
                code="SUPER_ADMIN_HAS_NO_VILLAGES",
            )
        await _set_user_villages(db, user_id, data["village_ids"])

    await db.flush()
    return await _to_user_out(db, user)


async def delete_user(db: AsyncSession, user_id: int, *, acting_user_id: int) -> None:
    if user_id == acting_user_id:
        raise ApiError("자기 계정은 삭제할 수 없습니다.", code="CANNOT_DELETE_SELF")

    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFound()

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
