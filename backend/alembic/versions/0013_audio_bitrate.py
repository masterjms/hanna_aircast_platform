"""오디오 비트레이트 설정 (라이브 opus · 파일 mp3).

문제점 리스트 29·30번 (2026-09-06).

라이브 스트림과 파일함 mp3 가 16kHz mono 24kbps 로 코드에 박혀 있었다. 표본율과
채널은 통신 사양 고정이라 그대로 두고 비트레이트만 설정에서 16 / 24 중에 고른다.

단말 CONFIG 로는 나가지 않는다 — opus 와 mp3 모두 자기 헤더에 비트레이트가 들어
있어 단말이 미리 알 필요가 없다. 그래서 config_version 도 올리지 않는다.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("live_bitrate_kbps", "file_bitrate_kbps"):
        op.add_column(
            "current_config",
            sa.Column(column, sa.SmallInteger(), nullable=False, server_default="24"),
        )


def downgrade() -> None:
    op.drop_column("current_config", "file_bitrate_kbps")
    op.drop_column("current_config", "live_bitrate_kbps")
