"""관리자 2단계 인증(TOTP) (문제점 70번 2항, 2026-10-18)

Revision ID: 0029
Revises: 0028

· users.totp_secret: OTP 앱과 나눠 가진 비밀(base32) — 단말 비밀번호처럼 AES-GCM 암호문(enc1:…).
  등록을 시작하면 채워지고, 앱의 6자리로 확인해야 totp_enabled_at 이 찍혀 켜진다.
· users.totp_enabled_at: 켠 시각. NULL 이면 꺼짐.
· users.totp_last_step: 마지막으로 받은 30초 칸 — 같은 숫자 재사용 거절.
· users.totp_recovery: 일회용 복구 코드 SHA-256 목록(쓰면 빠진다).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "totp_recovery", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "totp_recovery")
    op.drop_column("users", "totp_last_step")
    op.drop_column("users", "totp_enabled_at")
    op.drop_column("users", "totp_secret")
