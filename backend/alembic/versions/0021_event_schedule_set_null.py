"""방송 기록이 스케줄 삭제를 막지 않게 — broadcast_events.schedule_id ON DELETE SET NULL.

문제점 리스트 43번 (2026-09-29).

0001 에서 broadcast_events.schedule_id 를 제약 없는 FK 로 만들어, **한 번이라도 실행된
스케줄은 삭제가 거부됐다**(IntegrityError → 500 → 화면 「삭제에 실패했습니다」). 그런데
단말·마을·파일 삭제는 그 스케줄이 대상이면 막으므로(SCHEDULE_TARGET_IN_USE · FILE_IN_USE)
둘이 서로를 막아 어느 쪽도 지울 수 없었다.

이력은 불변 로그라 행은 남기고 "어느 스케줄이었는지" 만 지운다 — 계정(0014)·파일과 같은
방침. 방송 기록은 file_name·대상을 제 행에 스냅샷으로 갖고 있어 화면 표시에 지장이 없다.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

#: 0001 에서 이름 없이 만들어 PG 기본 규칙을 따른다(0014 와 같은 방식).
_NAME = "broadcast_events_schedule_id_fkey"


def upgrade() -> None:
    op.drop_constraint(_NAME, "broadcast_events", type_="foreignkey")
    op.create_foreign_key(
        _NAME, "broadcast_events", "schedules", ["schedule_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(_NAME, "broadcast_events", type_="foreignkey")
    op.create_foreign_key(_NAME, "broadcast_events", "schedules", ["schedule_id"], ["id"])
