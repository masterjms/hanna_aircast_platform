"""자동방송 스케줄 — 규칙 모델 교체와 실행 기록.

스케줄 설계 2026-09-09.

옛 schedules 는 months[] + weekdays[] + times[] 조합이었다. 화면 사양(매일·매주·매월·
매년 중 하나 + 시각 하나)을 그걸로는 표현할 수 없어(매월 15일, 매년 3월 1일) 갈아엎는다.
기능이 미구현이라 표에 데이터가 없다.

schedules
  repeat        daily | weekly | monthly | yearly
  weekdays      weekly 용 (0=일 … 6=토)
  month_days    monthly 용 (1~31)
  year_dates    yearly 용 [{month, day}]
  fire_time     시각 하나 (KST)
  target_scope  village | device | organization   ← organization 은 실행 시점에 마을로 펼친다
  store_flash

schedule_runs — 걸린 회차의 기록. UNIQUE(schedule_id, fire_at) 가 중복 실행 방지의 실체다.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("schedules", "months")
    op.drop_column("schedules", "weekdays")
    op.drop_column("schedules", "times")

    op.add_column("schedules", sa.Column("repeat", sa.String(length=10), nullable=False))
    op.add_column("schedules", sa.Column("weekdays", postgresql.ARRAY(sa.Integer()), nullable=True))
    op.add_column("schedules", sa.Column("month_days", postgresql.ARRAY(sa.Integer()), nullable=True))
    op.add_column("schedules", sa.Column("year_dates", postgresql.JSONB(), nullable=True))
    op.add_column("schedules", sa.Column("fire_time", sa.Time(), nullable=False))
    op.add_column(
        "schedules",
        sa.Column("store_flash", sa.Boolean(), nullable=False, server_default="false"),
    )

    op.drop_constraint("ck_schedules_target_scope", "schedules", type_="check")
    op.create_check_constraint(
        "ck_schedules_target_scope",
        "schedules",
        "target_scope IN ('village', 'device', 'organization')",
    )
    op.create_check_constraint(
        "ck_schedules_repeat",
        "schedules",
        "repeat IN ('daily', 'weekly', 'monthly', 'yearly')",
    )

    op.create_table(
        "schedule_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Integer(),
            sa.ForeignKey("schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column(
            "event_id",
            sa.BigInteger(),
            sa.ForeignKey("broadcast_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("schedule_id", "fire_at", name="uq_schedule_runs_schedule_fire"),
    )
    op.create_index("ix_schedule_runs_schedule_id", "schedule_runs", ["schedule_id"])


def downgrade() -> None:
    op.drop_index("ix_schedule_runs_schedule_id", table_name="schedule_runs")
    op.drop_table("schedule_runs")

    op.drop_constraint("ck_schedules_repeat", "schedules", type_="check")
    op.drop_constraint("ck_schedules_target_scope", "schedules", type_="check")
    op.create_check_constraint(
        "ck_schedules_target_scope",
        "schedules",
        "target_scope IN ('device', 'zone', 'village', 'all')",
    )

    for col in ("store_flash", "fire_time", "year_dates", "month_days", "weekdays", "repeat"):
        op.drop_column("schedules", col)

    op.add_column("schedules", sa.Column("months", postgresql.ARRAY(sa.Integer()), nullable=False))
    op.add_column("schedules", sa.Column("weekdays", postgresql.ARRAY(sa.Integer()), nullable=False))
    op.add_column("schedules", sa.Column("times", postgresql.ARRAY(sa.Time()), nullable=False))
