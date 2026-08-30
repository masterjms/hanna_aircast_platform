"""등록(QR 스캔) 시점의 하드웨어 식별값 4종 추가.

QR 5필드(생산 사양 §3.2) 중 MAC 뒤의 P4/C6 모델·버전. 출하 당시 값이라
STATUS 의 실행 중 버전(p4_fw/c6_fw)과 별개로 보관한다 — 펌웨어 이력 확인용.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_COLUMNS = ("p4_model", "p4_version", "c6_model", "c6_version")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("devices", sa.Column(name, sa.String(50), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("devices", name)
