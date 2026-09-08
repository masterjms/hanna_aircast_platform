"""관리자 계층 — 누가 무엇을 관리할 수 있는가.

관리자 계층 설계(2026-09-08) §2·§4·§5 의 구현이다.

  최고(3) > 시·도(2) > 시·군(1) > 마을(0)

권한은 조직 트리(organizations)를 따른다. 주소(b_code)는 트리의 입력값이 아니다 —
마을을 만들 때 기관을 제안하는 데만 쓴다(§1).

이 모듈은 두 가지만 한다:
  · 역할 → 마을 범위(VillageScope). 그 아래 계층(조회 필터·겹침 검사·MQTT 2중 방어)은
    마을 id 집합만 보므로 바뀌지 않는다.
  · 역할 사이의 순서. "나보다 낮은 계층의 계정만 만든다" 같은 판정.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import Role
from app.core.scope import VillageScope
from app.models.org import Organization, User, UserVillage, Village

#: 계층 순서. 숫자가 클수록 위다.
ROLE_TIER: dict[str, int] = {
    Role.SUPER_ADMIN.value: 3,
    Role.SIDO_ADMIN.value: 2,
    Role.SIGUNGU_ADMIN.value: 1,
    Role.VILLAGE_ADMIN.value: 0,
}

#: 기관에 소속되는 역할. 나머지는 organization_id 가 NULL 이어야 한다.
ORG_ROLES = frozenset({Role.SIDO_ADMIN.value, Role.SIGUNGU_ADMIN.value})

#: 마을·구역·계정을 관리할 수 있는 역할(시·군 이상).
ORG_ADMIN_ROLES = frozenset(
    {Role.SUPER_ADMIN.value, Role.SIDO_ADMIN.value, Role.SIGUNGU_ADMIN.value}
)


def tier(role: str) -> int:
    return ROLE_TIER.get(role, -1)


def manageable_roles(role: str) -> frozenset[str]:
    """이 역할이 만들고 고칠 수 있는 역할들 — 자기보다 낮은 계층 전부(§5)."""
    mine = tier(role)
    return frozenset(r for r, t in ROLE_TIER.items() if t < mine)


async def org_ids_under(db: AsyncSession, user: User) -> set[int] | None:
    """이 계정이 다스리는 기관 id 집합. super_admin 은 None(전체).

    시·도는 자기 기관과 그 바로 아래 기관들, 시·군은 자기 기관뿐이다. 기관 트리는
    두 단계라(§3) 재귀 질의를 쓰지 않는다. 기관이 없는 시·도/시·군 계정은 빈 집합.
    """
    if user.role == Role.SUPER_ADMIN.value:
        return None
    if user.role not in ORG_ROLES or user.organization_id is None:
        return set()
    ids = {user.organization_id}
    if user.role == Role.SIDO_ADMIN.value:
        children = await db.scalars(
            select(Organization.id).where(Organization.parent_id == user.organization_id)
        )
        ids.update(children.all())
    return ids


async def resolve_scope(db: AsyncSession, user: User) -> VillageScope:
    """역할 → 마을 범위 (§4)."""
    if user.role == Role.SUPER_ADMIN.value:
        return VillageScope.for_super_admin()

    if user.role == Role.VILLAGE_ADMIN.value:
        rows = await db.scalars(
            select(UserVillage.village_id).where(UserVillage.user_id == user.id)
        )
        return VillageScope.for_villages(rows.all())

    orgs = await org_ids_under(db, user)
    if not orgs:
        return VillageScope.for_villages([])
    rows = await db.scalars(select(Village.id).where(Village.organization_id.in_(orgs)))
    return VillageScope.for_villages(rows.all())

