"""파일 대기 설정 통합(file_wait_sec) + 파일 방송 재생 국면.

문제점 리스트 8·10번(2026-09-03). 0010 이 이미 운영에 적용된 뒤에 설계가 바뀌어
0010 을 고치지 않고 여기서 이어 간다(적용된 마이그레이션은 손대지 않는다).

current_config
  · file_result_wait_sec (0010 이 추가) — 삭제. 파일 대기를 시작·중지로 쪼갰던 것을 되돌린다.
  · file_stop_wait_sec (0008) → **file_wait_sec** 로 개명, 기본 120. 파일 방송 **시작과
    중지에 같이** 쓴다(문제점 8번, 단말 요청 §3.4 "서버가 관리할 값은 셋"). 시작 후에는
    FILE_RESULT ok=true(저장 완료 → 재생 시작)를, 중지 후에는 종료 응답을 기다린다.
    옛 범위(10~30)는 3MB 저장(약 40초)을 실패로 봤으므로 30 미만 값은 120 으로 올린다.
    단말 자체 포기가 120초라 그 이상은 무의미하다.

broadcast_events — 파일 방송의 "끝"은 FILE_RESULT 가 아니다(문제점 10번):
  · autoplay — False 면 저장만 하니 FILE_RESULT 가 곧 끝. True 면 그 뒤 재생이 이어진다.
  · playing_since — 전 단말이 저장을 마쳐 재생으로 넘어간 시각. 여기서 재생 길이 뒤에
    종료로 확정한다. 예전에는 FILE_RESULT 에 바로 끝내서 스피커가 나오는 중에 화면은
    "종료"였다.

⚠ 조건부로 쓴다. 이 파일을 만들기 전 잠깐 동안 "0010 을 덧쓴 버전"(개명·autoplay 까지
  한 번에 하는)이 리포에 있었다. 어느 쪽 0010 이 적용됐든 같은 결과로 끝나게, 지금
  스키마를 보고 필요한 것만 한다. 정상 경로(운영: 아침 0010 적용)에서는 전부 실행된다.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    cfg = _columns("current_config")

    if "file_result_wait_sec" in cfg:
        op.drop_column("current_config", "file_result_wait_sec")

    if "file_wait_sec" not in cfg:
        op.alter_column(
            "current_config",
            "file_stop_wait_sec",
            new_column_name="file_wait_sec",
            server_default="120",
        )
    # 옛 범위(10~30)에 있던 값은 새 최솟값(30) 아래다. 정상 저장을 실패로 보던 값이라
    # 새 기본으로 올린다. 30 이상으로 손수 올려 둔 값이면 그대로 둔다.
    op.execute("UPDATE current_config SET file_wait_sec = 120 WHERE file_wait_sec < 30")

    ev = _columns("broadcast_events")
    if "autoplay" not in ev:
        op.add_column("broadcast_events", sa.Column("autoplay", sa.Boolean(), nullable=True))
    if "playing_since" not in ev:
        op.add_column(
            "broadcast_events",
            sa.Column("playing_since", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("broadcast_events", "playing_since")
    op.drop_column("broadcast_events", "autoplay")
    # 120 은 옛 범위(10~30)를 벗어난다 — 옛 코드의 검증에 걸리지 않게 옛 기본으로 되돌린다.
    op.execute("UPDATE current_config SET file_wait_sec = 10 WHERE file_wait_sec > 30")
    op.alter_column(
        "current_config",
        "file_wait_sec",
        new_column_name="file_stop_wait_sec",
        server_default="10",
    )
    # 0010 의 downgrade 가 이 컬럼을 지우므로 되살려 둔다.
    op.add_column(
        "current_config",
        sa.Column("file_result_wait_sec", sa.SmallInteger(), nullable=False, server_default="120"),
    )
