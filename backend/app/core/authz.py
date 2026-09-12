"""관리자 계층 — 누가 무엇을 관리할 수 있는가.

관리자 계층 설계(2026-09-08, 2026-09-12 v2) §2·§4·§5 의 구현이다.

  최고(2) > 기관(1) > 마을(0)

권한은 조직 트리(organizations)를 따른다. 트리는 깊이 제한이 없고(v2), 기관 관리자의
관할은 **소속 기관과 그 아래 전부**(부분 트리)다. 기관 관리자끼리의 위아래는 역할
이름이 아니라 트리 위치가 정한다 — 상위 노드의 관리자가 하위 노드의 관리자를
관리한다. 같은 노드의 관리자끼리는 서로를 만지지 못한다.

주소(b_code)는 트리의 입력값이 아니다 — 마을의 위치일 뿐이다(§1).

이 모듈은 두 가지만 한다:
  · 역할 → 마을 범위(VillageScope). 그 아래 계층(조회 필터·겹침 검사·MQTT 2중 방어)은
    마을 id 집합만 보므로 바뀌지 않는다.
  · 역할 사이의 순서. "나보다 낮은 계층의 계정만 만든다" 같은 판정.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import Role
from app.core.orgtree import load_tree
from app.core.scope import VillageScope
from app.models.org import User, UserVillage, Village

#: 계층 순서. 숫자가 클수록 위다.
ROLE_TIER: dict[str, int] = {
    Role.SUPER_ADMIN.value: 2,
    Role.ORG_ADMIN.value: 1,
    Role.VILLAGE_ADMIN.value: 0,
}

#: 기관에 소속되는 역할. 나머지는 organization_id 가 NULL 이어야 한다.
ORG_ROLES = frozenset({Role.ORG_ADMIN.value})

#: 마을·기관·계정을 관리할 수 있는 역할(기관 관리자 이상).
ORG_ADMIN_ROLES = frozenset({Role.SUPER_ADMIN.value, Role.ORG_ADMIN.value})


def tier(role: str) -> int:
    return ROLE_TIER.get(role, -1)


def manageable_roles(role: str) -> frozenset[str]:
    """이 역할이 만들고 고칠 수 있는 역할들(§5).

    최고 관리자는 최고 관리자를 만들지 않는다(예전과 같다 — 최고 계정은 배포 때 만든다).
    기관 관리자는 **기관 관리자도** 만든다 — 단, 자기 아래 노드에만. 그 위치 검사는
    역할표로는 못 하므로 org 서비스가 트리로 한다(_check_role_shape).
    """
    if role == Role.SUPER_ADMIN.value:
        return frozenset({Role.ORG_ADMIN.value, Role.VILLAGE_ADMIN.value})
    if role == Role.ORG_ADMIN.value:
        return frozenset({Role.ORG_ADMIN.value, Role.VILLAGE_ADMIN.value})
    return frozenset()


async def org_ids_under(db: AsyncSession, user: User) -> set[int] | None:
    """이 계정이 다스리는 기관 id 집합 = 소속 기관의 부분 트리. super_admin 은 None(전체).

    기관이 없는 기관 관리자와 이장은 빈 집합.
    """
    if user.role == Role.SUPER_ADMIN.value:
        return None
    if user.role not in ORG_ROLES or user.organization_id is None:
        return set()
    tree = await load_tree(db)
    return tree.subtree(user.organization_id)


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
