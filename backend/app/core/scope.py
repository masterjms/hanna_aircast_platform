"""담당 마을 범위(VillageScope).

권한 필터를 리스트나 None 으로 들고 다니면 "None = 전체"인지 "None = 없음"인지
헷갈려서 결국 사고가 난다. 그래서 전체 접근 여부를 타입 안에 못 박은 값 객체를 쓴다.

규칙:
  · 조회 API 는 반드시 scope.apply() 를 통과시킨 뒤 응답을 만든다.
  · 제어 API 는 반드시 scope.ensure_allowed() 로 대상을 검사한 뒤 발행한다.
  · MQTT 발행 직전에 publisher 가 한 번 더 확인한다(2중 방어).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, Select

from app.constants import Role
from app.errors import VillageOutOfScope


@dataclass(frozen=True, slots=True)
class VillageScope:
    """로그인 계정이 다룰 수 있는 마을의 범위."""

    #: super_admin 이면 True. 이때 village_ids 는 비어 있고 의미가 없다.
    all_villages: bool
    village_ids: frozenset[int] = field(default_factory=frozenset)

    @classmethod
    def for_super_admin(cls) -> VillageScope:
        return cls(all_villages=True)

    @classmethod
    def for_villages(cls, ids: Iterable[int]) -> VillageScope:
        return cls(all_villages=False, village_ids=frozenset(ids))

    # ── 판정 ────────────────────────────────────────────────────────────
    def allows(self, village_id: int | None) -> bool:
        """이 마을을 다룰 수 있는가.

        village_id=None 은 '미배정 단말'을 뜻한다. 미배정은 어느 마을에도 속하지 않으므로
        super_admin 만 다룰 수 있다.
        """
        if self.all_villages:
            return True
        if village_id is None:
            return False
        return village_id in self.village_ids

    def ensure_allowed(self, village_id: int | None) -> None:
        """다룰 수 없으면 403 을 던진다."""
        if not self.allows(village_id):
            raise VillageOutOfScope(detail={"village_id": village_id})

    @property
    def is_empty(self) -> bool:
        """담당 마을이 하나도 없는 village_admin. 조회 결과는 항상 빈 집합이다."""
        return not self.all_villages and not self.village_ids

    # ── 질의 필터 ───────────────────────────────────────────────────────
    def apply(self, stmt: Select, column: ColumnElement) -> Select:
        """SELECT 문에 마을 범위 조건을 건다.

            stmt = scope.apply(select(Device), Device.village_id)

        super_admin 이면 그대로 통과시키고, village_admin 이면 IN 조건을 건다.
        담당 마을이 없으면 IN () 이 되어 빈 결과가 나온다 — 의도한 동작이다.
        """
        if self.all_villages:
            return stmt
        return stmt.where(column.in_(self.village_ids))

    # ── 표시용 ──────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "all_villages": self.all_villages,
            "village_ids": sorted(self.village_ids),
        }


def scope_for(role: str, village_ids: Iterable[int]) -> VillageScope:
    """역할 + 담당 마을 목록 → VillageScope."""
    if role == Role.SUPER_ADMIN.value:
        return VillageScope.for_super_admin()
    return VillageScope.for_villages(village_ids)
