"""기관 트리 — 깊이 제한 없는 organizations 를 한 번 읽어 메모리에서 탄다.

2026-09-12 결정(관리자 계층 설계 v2): 계층 깊이에 한계를 두지 않는다. 도 > 시 > 구 >
권역 > 마을처럼 5단으로 써도 되고 기관 하나 아래 마을만 둬도 된다. 그래서 "시·도는
바로 아래까지"처럼 단수를 세는 코드가 전부 사라지고, 모든 판정이 **부분 트리**(자기
자신 + 모든 후손) 하나로 모인다.

재귀 CTE 대신 전체를 한 번 읽는 이유:
  · 기관은 많아야 수백 개다. (id, parent_id, name) 세 칼럼이면 수십 KB 다.
  · 요청마다 질의 한 번으로 끝나서, 조회 수가 기관 수·깊이와 무관하다
    (현행 문서 README 「목록 API 질의 비증가」 규칙).
  · 순환·고아 같은 깨진 데이터를 파이썬에서 방어할 수 있다. CTE 는 순환에서 멈추지
    않는다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.org import Organization

#: 트리 모양을 바꾸는 쓰기를 한 줄로 세우는 어드바이저리 락 키. 이 서버 안에서 유일하면 된다.
ORG_TREE_LOCK_KEY = 0x78776966_6F726774  # "xwiforgt"


async def lock_tree(db: AsyncSession) -> None:
    """트리를 바꾸는 쓰기(기관 추가·옮기기·삭제, 마을·계정을 기관에 붙이기)를 직렬화한다.

    순환 검사는 "지금 트리"를 읽고 판정한다. 두 요청이 동시에 X→Y 아래, Y→X 아래로
    옮기면 서로 커밋 전 상태를 못 보고 둘 다 통과해 순환이 생긴다 — 그러면 두 기관 모두
    뿌리가 없어져 지역 관리 화면에서 사라진다(2026-09-15 실제 DB 로 재현). 삭제도 같다:
    빈 기관인지 센 뒤 지우는 사이에 다른 요청이 마을을 붙일 수 있다.

    트랜잭션 락이라 커밋·롤백 때 풀린다. 구조 변경은 사람이 가끔 하는 일이라 비용이 없다.
    반드시 트리를 읽기 **전에** 잡는다.
    """
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ORG_TREE_LOCK_KEY})


@dataclass(frozen=True)
class OrgTree:
    """읽기 전용 스냅샷. 요청 하나 안에서만 쓴다 — 캐시하지 않는다."""

    parent: dict[int, int | None] = field(default_factory=dict)
    name: dict[int, str] = field(default_factory=dict)
    children: dict[int, tuple[int, ...]] = field(default_factory=dict)

    @classmethod
    def build(cls, rows: Iterable[tuple[int, int | None, str]]) -> OrgTree:
        parent: dict[int, int | None] = {}
        name: dict[int, str] = {}
        kids: dict[int, list[int]] = {}
        for org_id, parent_id, org_name in rows:
            parent[org_id] = parent_id
            name[org_id] = org_name
            if parent_id is not None:
                kids.setdefault(parent_id, []).append(org_id)
        # 자식은 이름순 — 화면과 라벨이 같은 순서를 쓰게.
        children = {
            pid: tuple(sorted(ids, key=lambda i: (name.get(i, ""), i))) for pid, ids in kids.items()
        }
        return cls(parent=parent, name=name, children=children)

    def __contains__(self, org_id: object) -> bool:
        return org_id in self.parent

    def roots(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                (i for i, p in self.parent.items() if p is None or p not in self.parent),
                key=lambda i: (self.name.get(i, ""), i),
            )
        )

    def subtree(self, root: int) -> set[int]:
        """root 와 그 모든 후손. root 가 없으면 빈 집합. 순환이 있어도 멈춘다."""
        if root not in self.parent:
            return set()
        seen: set[int] = set()
        stack = [root]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.children.get(cur, ()))
        return seen

    def subtree_of(self, roots: Iterable[int]) -> set[int]:
        out: set[int] = set()
        for r in roots:
            out |= self.subtree(r)
        return out

    def ancestors(self, org_id: int) -> list[int]:
        """자기 제외, 가까운 것부터 뿌리까지. 순환이면 한 바퀴에서 멈춘다."""
        out: list[int] = []
        seen = {org_id}
        cur = self.parent.get(org_id)
        while cur is not None and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = self.parent.get(cur)
        return out

    def path(self, org_id: int) -> list[int]:
        """뿌리 → 자기. 화면의 breadcrumb 이 쓴다."""
        if org_id not in self.parent:
            return []
        return [*reversed(self.ancestors(org_id)), org_id]

    def path_names(self, org_id: int, sep: str = " › ") -> str:
        return sep.join(self.name[i] for i in self.path(org_id) if i in self.name)

    def is_descendant(self, org_id: int, of: int) -> bool:
        """org_id 가 of 의 **진**후손인가(자기 자신은 아니다)."""
        return org_id != of and of in self.ancestors(org_id)

    def would_cycle(self, org_id: int, new_parent: int | None) -> bool:
        """org_id 의 부모를 new_parent 로 바꾸면 순환이 생기는가.

        자기 자신이나 자기 후손을 부모로 두면 순환이다.
        """
        if new_parent is None:
            return False
        return new_parent == org_id or new_parent in self.subtree(org_id)

    def depth(self, org_id: int) -> int:
        """뿌리가 0."""
        return len(self.ancestors(org_id))


async def load_tree(db: AsyncSession) -> OrgTree:
    rows = (
        await db.execute(select(Organization.id, Organization.parent_id, Organization.name))
    ).all()
    return OrgTree.build(rows)
