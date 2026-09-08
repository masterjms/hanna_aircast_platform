"""관리자 4계층 — 기관(organizations) 트리와 시·도/시·군 역할.

관리자 계층 설계 2026-09-08.

권한은 조직 트리를 따르고 주소는 트리의 입력값이 아니다(설계 §1). 그래서 마을에는
"어디에 있나"(b_code, 그대로)와 별개로 "누가 관리하나"(organization_id)가 생긴다.

organizations      — 시·도(sido) / 시·군(sigungu) 두 수준. sigungu 의 parent 는 sido.
                     jurisdiction_code 는 마을을 만들 때 기관을 제안하는 데만 쓴다.
villages.organization_id — 관리 기관. NULL 이면 최고 관리자만 본다.
users.organization_id    — sido_admin·sigungu_admin 의 소속. 나머지 역할은 NULL.
users.role CHECK         — sido_admin, sigungu_admin 추가.

기존 계정·마을은 값이 NULL 인 채로 지금과 똑같이 동작한다. FK 는 RESTRICT 다 —
소속 마을·계정이 있는 기관은 API 가 먼저 막고(ORGANIZATION_IN_USE) DB 가 한 번 더 막는다.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_OLD_ROLES = "role IN ('super_admin', 'village_admin')"
_NEW_ROLES = "role IN ('super_admin', 'sido_admin', 'sigungu_admin', 'village_admin')"


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("jurisdiction_code", sa.String(length=5), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("level IN ('sido', 'sigungu')", name="ck_organizations_level"),
    )
    op.create_index("ix_organizations_parent_id", "organizations", ["parent_id"])

    op.add_column(
        "villages",
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index("ix_villages_organization_id", "villages", ["organization_id"])

    op.add_column(
        "users",
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )

    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", _NEW_ROLES)


def downgrade() -> None:
    # 새 역할 계정이 남아 있으면 옛 제약을 못 건다 — 내리기 전에 사람이 정리한다.
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", _OLD_ROLES)

    op.drop_column("users", "organization_id")
    op.drop_index("ix_villages_organization_id", table_name="villages")
    op.drop_column("villages", "organization_id")
    op.drop_index("ix_organizations_parent_id", table_name="organizations")
    op.drop_table("organizations")
