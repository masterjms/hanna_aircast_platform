"""단말별 MQTT 계정 비밀번호 열 추가.

username 은 MAC(기본키) 그대로라 password 만 저장한다. NULL = 미발행.
사양: docs/spec/SERVER_DEVICE_CREDENTIAL_SPEC_2026-08-27.md

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("mqtt_password", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "mqtt_password")
