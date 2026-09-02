"""방송 종료 판정 — 중지 응답 대기 · 대상 대수 기록.

문제점 리스트 3·4·5번(2026-09-02):
  · 파일 방송이 단말에서 끝나도 UI 는 계속 "방송 중"이었다. 종료 결과를 세려면
    분모(발행 시점 대상 대수)가 필요해서 `expected_count` 를 남긴다.
  · 중지를 누르면 단말 응답을 안 보고 즉시 종료로 처리했다. 이제 `stop_requested_at`
    을 찍고 기다렸다가, 다 응답하거나 대기 시간이 지나면 `ended_at` 을 찍는다.
  · 대기 시간은 설정에서 바꾼다(10~30초, 기본 10초). 파일/라이브를 따로 둔다.

`current_config` 에 넣지만 단말로 나가는 값이 아니다 — 그래서 이 두 값만 바뀔 때는
config_version 을 올리지 않는다(올리면 전 단말이 의미 없는 CONFIG 를 다시 받는다).

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broadcast_events",
        sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("broadcast_events", sa.Column("expected_count", sa.Integer(), nullable=True))
    op.add_column(
        "current_config",
        sa.Column("file_stop_wait_sec", sa.SmallInteger(), nullable=False, server_default="10"),
    )
    op.add_column(
        "current_config",
        sa.Column("live_stop_wait_sec", sa.SmallInteger(), nullable=False, server_default="10"),
    )


def downgrade() -> None:
    op.drop_column("current_config", "live_stop_wait_sec")
    op.drop_column("current_config", "file_stop_wait_sec")
    op.drop_column("broadcast_events", "expected_count")
    op.drop_column("broadcast_events", "stop_requested_at")
