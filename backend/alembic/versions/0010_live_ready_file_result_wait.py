"""current_config 에 라이브 준비 제한 · 파일 저장 완료 대기 추가.

단말 요청 `SERVER_BROADCAST_STOP_SEQUENCE_2026-09-03.md` §3.4 — 서버가 관리할 값은
셋이다: 라이브 준비 제한(단말에 전달), 라이브 종료 대기, 파일 대기.

  · live_ready_timeout_sec (기본 30, 사양 1~60) — LIVE_START.ready_timeout_sec 로
    나간다. 단말은 이 값 + 5초까지 기다렸다가 LIVE_READY 를 보내므로, 서버 화면의
    "준비 지연" 기준은 설정이 아니라 이 값 + 5 로 계산한다(+5 는 펌웨어 상수).
  · file_result_wait_sec (기본 120) — 파일 시작 후 FILE_RESULT 상한. FILE_RESULT 는
    "받아서 저장까지 끝냈다"는 신호라 재생 길이와 무관하고, 저장이 느리다
    (LittleFS 80~100KB/s, 3MB 면 40초). 단말 자체 포기가 120초라 그 이상은 무의미.

둘 다 CONFIG 토픽으로 나가지 않으므로 바꿔도 config_version 을 올리지 않는다.
(라이브 종료 대기 live_stop_wait_sec 는 0008 에 이미 있다.)

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "current_config",
        sa.Column("live_ready_timeout_sec", sa.SmallInteger(), nullable=False, server_default="30"),
    )
    op.add_column(
        "current_config",
        sa.Column("file_result_wait_sec", sa.SmallInteger(), nullable=False, server_default="120"),
    )


def downgrade() -> None:
    op.drop_column("current_config", "file_result_wait_sec")
    op.drop_column("current_config", "live_ready_timeout_sec")
