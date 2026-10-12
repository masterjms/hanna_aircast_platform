"""비밀번호 정책 — 변경 시각·연속 실패 잠금 (문제점 70번 ②, 2026-10-12)

Revision ID: 0027
Revises: 0026

· password_changed_at: 마지막으로 비밀번호를 정한 시각. PASSWORD_MAX_AGE_DAYS(기본 90일)가 지나면
  로그인 때 「새 비밀번호 정하기」로 보낸다. 기존 계정은 마이그레이션 시각으로 채워 90일을 준다.
· failed_login_count · locked_until: 비밀번호를 LOGIN_LOCK_THRESHOLD(기본 5)번 연속 틀리면
  LOGIN_LOCK_MINUTES(기본 30분) 동안 로그인을 막는다. 성공하면 0 으로 돌아간다.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "password_changed_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "users",
        sa.Column("failed_login_count", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
    op.drop_column("users", "password_changed_at")
