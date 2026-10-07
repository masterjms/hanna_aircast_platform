"""CONFIG 재조정 주기를 설정 화면에서 고른다 — current_config.config_reconcile_hours (문제점 63번, 2026-10-07)

Revision ID: 0024
Revises: 0023

재조정은 기동 때 1회 + 주기마다 공통 CONFIG 와 단말별 CONFIG(retained)를 DB 값으로 다시
발행하는 안전망이다. 주기가 .env(CONFIG_RECONCILE_INTERVAL_SEC)에만 있어 운영자가 바꿀 수
없었다. 단말 CONFIG 로 나가는 값이 아니므로 config_version 은 올리지 않는다.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "current_config",
        sa.Column("config_reconcile_hours", sa.SmallInteger(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("current_config", "config_reconcile_hours")
