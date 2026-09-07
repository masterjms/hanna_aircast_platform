"""계정 사용 기간 + 계정 삭제 시 참조 정리.

문제점 리스트 26번 (2026-09-06).

users.expires_at — 계정을 만들 때 정한 사용 기간의 끝. NULL 이면 무기한이라 기존
계정은 전부 그대로 남는다. 만료된 계정은 로그인이 막히고 정리 작업이 지운다.

FK 를 ON DELETE SET NULL 로 바꾸는 이유: files.uploaded_by, broadcast_events.
triggered_by, schedules.created_by 가 제약 없는 FK 라 파일을 올렸거나 방송을 한 번이라도
한 계정은 **삭제 자체가 거부된다**. 자동 삭제가 매번 실패하게 되므로 같이 고친다.
이력은 불변 로그라 행은 남기고 "누가" 만 지운다(device_events FK 를 뺐던 0006 과 같은
방침). 계정별 파일함·이력·스케줄 분리(문제점 28번)는 역할 사양이 정해진 뒤 따로 다룬다.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

#: (테이블, 컬럼, 제약 이름). 0001 에서 이름 없이 만들어 PG 기본 규칙을 따른다.
_USER_REFS = [
    ("files", "uploaded_by", "files_uploaded_by_fkey"),
    ("broadcast_events", "triggered_by", "broadcast_events_triggered_by_fkey"),
    ("schedules", "created_by", "schedules_created_by_fkey"),
]


def upgrade() -> None:
    op.add_column("users", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_expires_at", "users", ["expires_at"])

    for table, column, name in _USER_REFS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, "users", [column], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    for table, column, name in _USER_REFS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, "users", [column], ["id"])

    op.drop_index("ix_users_expires_at", table_name="users")
    op.drop_column("users", "expires_at")
