"""기관 트리 깊이 무제한 · 기관 역할 하나로 통합.

관리자 계층 설계 v2 (2026-09-12). 계층 깊이에 한계를 두지 않기로 했다 — 도 > 시 > 구 >
권역처럼 몇 단이든 되고 마을은 어느 마디에나 붙는다. 그래서:

organizations.level             삭제 — "시·도/시·군" 두 수준이 의미를 잃는다.
organizations.jurisdiction_code 삭제 — 주소로 기관을 제안하는 기능(공식/운영 구분)을 없앴다.
                                  마을만 주소를 갖고, 마디는 이름과 부모뿐이다.
users.role                      sido_admin·sigungu_admin → org_admin 하나. 기관 관리자끼리의
                                  위아래는 역할 이름이 아니라 트리 위치가 정한다.

기존 데이터: 계정은 역할 값만 바뀌고 소속 기관·범위는 그대로다(부분 트리 = 예전 "자기
+ 바로 아래"를 포함). 마을·단말·이력은 손대지 않는다.

downgrade 는 2단 트리로만 되돌릴 수 있다 — parent 가 없으면 sido, 있으면 sigungu 로
적고 org_admin 은 그 수준을 따른다. 3단 이상으로 쓴 뒤 내리면 ck_organizations_level 은
통과하지만 옛 코드의 "sigungu 의 부모는 sido" 가정은 깨진다. 내리기 전에 사람이 정리한다.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_OLD_ROLES = "role IN ('super_admin', 'sido_admin', 'sigungu_admin', 'village_admin')"
_NEW_ROLES = "role IN ('super_admin', 'org_admin', 'village_admin')"


def upgrade() -> None:
    op.execute("UPDATE users SET role = 'org_admin' WHERE role IN ('sido_admin', 'sigungu_admin')")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", _NEW_ROLES)

    op.drop_constraint("ck_organizations_level", "organizations", type_="check")
    op.drop_column("organizations", "level")
    op.drop_column("organizations", "jurisdiction_code")


def downgrade() -> None:
    op.add_column("organizations", sa.Column("jurisdiction_code", sa.String(length=5), nullable=True))
    op.add_column("organizations", sa.Column("level", sa.String(length=20), nullable=True))
    op.execute(
        "UPDATE organizations SET level = CASE WHEN parent_id IS NULL THEN 'sido' ELSE 'sigungu' END"
    )
    op.alter_column("organizations", "level", nullable=False)
    op.create_check_constraint(
        "ck_organizations_level", "organizations", "level IN ('sido', 'sigungu')"
    )

    op.drop_constraint("ck_users_role", "users", type_="check")
    op.execute(
        """
        UPDATE users u SET role = CASE
            WHEN o.parent_id IS NULL THEN 'sido_admin'
            ELSE 'sigungu_admin'
        END
        FROM organizations o
        WHERE u.role = 'org_admin' AND u.organization_id = o.id
        """
    )
    # 기관 없는 기관 관리자 — 옛 코드에서도 빈 범위였다. 낮은 쪽으로 둔다.
    op.execute("UPDATE users SET role = 'sigungu_admin' WHERE role = 'org_admin'")
    op.create_check_constraint("ck_users_role", "users", _OLD_ROLES)
