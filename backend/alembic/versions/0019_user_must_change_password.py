"""계정 — 임시 비밀번호 발급 뒤 첫 로그인에서 변경 강제.

향후검토 10번(2026-09-20). 계정 수정에서 비밀번호를 「확인」하고 싶다는 요청이었지만
비밀번호는 bcrypt 해시로만 저장해 원래 값을 꺼낼 수 없다. 대신 관리자가 임시 비밀번호를
발급해 한 번만 보여주고, 그 계정은 다음 로그인에서 새 비밀번호를 정하기 전까지 다른
기능을 쓸 수 없게 한다.

users.must_change_password  true 면 /api/auth/me · /api/auth/password · 로그아웃만 허용.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
