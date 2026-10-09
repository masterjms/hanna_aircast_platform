"""로그인 기록 — login_events (문제점 65번, 2026-10-09)

Revision ID: 0026
Revises: 0025

누가 언제 어디서 들어왔고(성공·실패), 언제 로그아웃했는지. 계정 관리 화면 아래 표로 보이고
(10·20·50건·쪽 넘기기), 관공서 요건(접속기록 보관)의 바탕이 된다. 보관은 LOGIN_RETENTION_DAYS
(기본 730일 = 2년) — 개인정보 안전성 확보조치 기준의 접속기록 보관(1년 이상, 대규모는 2년)에 맞춘다.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        #: ok · bad_password · unknown_user · expired
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("logged_in_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("logged_out_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_login_events_logged_in_at", "login_events", ["logged_in_at"])
    op.create_index("ix_login_events_user_id", "login_events", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_login_events_user_id", table_name="login_events")
    op.drop_index("ix_login_events_logged_in_at", table_name="login_events")
    op.drop_table("login_events")
