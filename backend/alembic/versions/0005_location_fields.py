"""위치 정보 — 마을 b_code·주소, 단말 주소·좌표.

지도 설계(docs/spec/xWIFI_지도_위치_설계_260831.md) §2.
단말 좌표는 전부 NULL 허용 — 비어 있으면 화면이 마을 좌표로 fallback 한다.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("villages", sa.Column("b_code", sa.String(10), nullable=True))
    op.add_column("villages", sa.Column("road_address", sa.String(255), nullable=True))
    op.add_column("villages", sa.Column("jibun_address", sa.String(255), nullable=True))

    op.add_column("devices", sa.Column("road_address", sa.String(255), nullable=True))
    op.add_column("devices", sa.Column("jibun_address", sa.String(255), nullable=True))
    op.add_column("devices", sa.Column("address_detail", sa.String(100), nullable=True))
    op.add_column("devices", sa.Column("lat", sa.Float(), nullable=True))
    op.add_column("devices", sa.Column("lng", sa.Float(), nullable=True))


def downgrade() -> None:
    for name in ("lng", "lat", "address_detail", "jibun_address", "road_address"):
        op.drop_column("devices", name)
    for name in ("jibun_address", "road_address", "b_code"):
        op.drop_column("villages", name)
