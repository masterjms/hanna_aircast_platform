"""broadcast_events.bytes_estimated 추가.

단말은 전송 바이트 수를 보고하지 않는다(통신 사양에 그런 필드가 없다). 트래픽
산정을 로그 grep 으로 역산해야 했던 문제(2026-09-02, D-1)를 없애려고, 방송이
정상 종료될 때 서버가 계산한 추정치를 같이 저장한다.
  · LIVE: 방송 시간 × 24kbps(사양 고정 비트레이트) × 수신 단말 수
  · FILE: 파일 크기 × 수신 단말 수
서버 재시작으로 고아가 되어(A-8) 정리된 행은 NULL로 남는다 — 시간을 신뢰할 수
없는 종료라 추정치를 넣으면 오히려 산정을 왜곡한다.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broadcast_events", sa.Column("bytes_estimated", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("broadcast_events", "bytes_estimated")
