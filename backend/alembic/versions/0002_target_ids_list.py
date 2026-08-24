"""target_id(단일 문자열) → target_ids(JSONB 목록).

"마을 2곳에 동시 방송" 같은 다중 대상은 String(50) 하나로 표현할 수 없다.
기존 값은 단일 원소 목록으로 옮긴다 — 이력의 의미가 바뀌지 않는다.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_TABLES = ("broadcast_events", "schedules")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("target_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        )
        # NULL(all 대상) → 빈 목록, 값 있음 → 단일 원소 목록
        op.execute(
            f"""
            UPDATE {table}
               SET target_ids = CASE
                     WHEN target_id IS NULL THEN '[]'::jsonb
                     ELSE jsonb_build_array(target_id)
                   END
            """
        )
        op.drop_column(table, "target_id")


def downgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("target_id", sa.String(50), nullable=True))
        # 다중 대상 이력은 첫 원소만 남는다 — 되돌리면 정보가 준다는 걸 감수한다.
        op.execute(
            f"""
            UPDATE {table}
               SET target_id = CASE
                     WHEN jsonb_array_length(target_ids) = 0 THEN NULL
                     ELSE target_ids->>0
                   END
            """
        )
        op.drop_column(table, "target_ids")
