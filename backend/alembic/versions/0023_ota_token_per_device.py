"""OTA 토큰을 단말마다 하나로 — 다 받아간 단말을 서버가 안다 (문제점 48번 보조설명, 2026-10-05)

Revision ID: 0023
Revises: 0022

실제 펌웨어는 패키지를 다 받으면 네트워크를 끊고 재부팅한다. 그래서 OTA_RESULT 가 오지
않고, 서버가 보는 "마지막 바이트까지 받아감" 이 OTA 의 성공 신호가 된다. 누가 받아갔는지
알려면 주소(토큰)가 단말마다 달라야 한다 — mac · fetched_at · completed_at · bytes_served.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ota_tokens", sa.Column("mac", sa.String(12), nullable=True))
    op.add_column("ota_tokens", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ota_tokens", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "ota_tokens",
        sa.Column("bytes_served", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index("ix_ota_tokens_job_id", "ota_tokens", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_ota_tokens_job_id", table_name="ota_tokens")
    op.drop_column("ota_tokens", "bytes_served")
    op.drop_column("ota_tokens", "completed_at")
    op.drop_column("ota_tokens", "fetched_at")
    op.drop_column("ota_tokens", "mac")
