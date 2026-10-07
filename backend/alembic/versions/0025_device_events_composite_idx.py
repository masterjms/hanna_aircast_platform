"""device_events 복합 인덱스 둘 — 방송 기록 LATERAL 과 응답 수 세기 (병목 감사 H2·H6, 2026-10-07)

Revision ID: 0025
Revises: 0024

· (event_id, mac, received_at DESC): 방송 기록이 (방송 × 단말)마다 "마지막 결과"를 찾는다. 기존
  event_id 인덱스만으로는 그 방송의 전 행(전체 방송이면 수천)을 읽고 메모리에서 골랐다.
· (event_id, result_type, mac): 결과가 올 때마다 "응답한 단말 수"를 센다(count distinct mac).
  인덱스만으로 세게 해서 결과 폭주 때 k번째 결과가 k행을 heap 에서 읽던 것을 줄인다.
"""

from __future__ import annotations

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_device_events_event_mac_received",
        "device_events",
        ["event_id", "mac", "received_at"],
        postgresql_ops={"received_at": "DESC"},
    )
    op.create_index(
        "ix_device_events_event_type_mac", "device_events", ["event_id", "result_type", "mac"]
    )


def downgrade() -> None:
    op.drop_index("ix_device_events_event_type_mac", table_name="device_events")
    op.drop_index("ix_device_events_event_mac_received", table_name="device_events")
