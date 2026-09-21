"""자동방송 — 「한 번만」 예약.

2026-09-21 방송 화면 개편. 이장님이 가장 많이 쓰는 예약은 「내일 아침 7시에 한 번」인데
반복은 매일·매주·매월·매년뿐이라 한 번만 거는 방법이 없었다(매년 규칙으로 걸면 내년에
또 나간다). 반복 종류에 once 를 더하고 그 날짜를 둔다.

schedules.once_date   once 일 때 그 날짜(KST). fire_time 과 합쳐 한 번만 나간다.
ck_schedules_repeat   'once' 추가.

기존 규칙은 아무것도 바뀌지 않는다.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_OLD = "repeat IN ('daily', 'weekly', 'monthly', 'yearly')"
_NEW = "repeat IN ('daily', 'weekly', 'monthly', 'yearly', 'once')"


def upgrade() -> None:
    op.add_column("schedules", sa.Column("once_date", sa.Date(), nullable=True))
    op.drop_constraint("ck_schedules_repeat", "schedules", type_="check")
    op.create_check_constraint("ck_schedules_repeat", "schedules", _NEW)


def downgrade() -> None:
    # once 규칙은 옛 코드가 해석하지 못한다 — 지우고 내린다.
    op.execute("DELETE FROM schedules WHERE repeat = 'once'")
    op.drop_constraint("ck_schedules_repeat", "schedules", type_="check")
    op.create_check_constraint("ck_schedules_repeat", "schedules", _OLD)
    op.drop_column("schedules", "once_date")
