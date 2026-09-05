"""village_code(법정동 12자리) 도입 · 파일 대기 범위 정정 · 미배정 CONFIG 재발행 준비.

문제점 리스트 16·18·19번 (2026-09-05).

villages.village_code — MQTT village_id 를 DB id 8자리에서 **법정동코드(10)+연번(2)
12자리**로 바꾼다(레지스트리 사양 §2.4, 2026-08-30 개정). b_code 가 있는 마을은
같은 리 안에서 id 순으로 01, 02… 를 받는다. 주소가 없는 마을은 NULL 로 남아 예전
방식(id 8자리)을 계속 쓴다. 한 번 만든 코드는 이후 바꾸지 않는다.

current_config.config_version + 1 — 토큰이 바뀐 마을의 단말은 새 village_id 를 받아
야 topic 구독을 옮긴다. 단말은 config_version 이 올라야 적용하므로 여기서 한 번
올린다. 기동 시 재조정(config_reconcile)이 새 버전으로 전 단말 CONFIG 를 내린다.
같은 버전 올림이 미배정 단말에도 "전부 0" CONFIG 를 명시해 보내는 계기가 된다
(문제점 18번 — 빈 retain 은 단말이 무시했다).

current_config.file_wait_sec — 기본 120 → **30**, 범위 30~180 → **10~60**. FILE_RESULT
는 "받아서 저장까지"가 아니라 "받고 무결성 검증까지"의 신호고 저장은 방송 중
백그라운드라 크기와 무관하다(단말 확인 2026-09-04, 716KB 실측 3.6초 — 문제점 19번).
0011 의 전제(3MB 저장 40초)가 틀렸던 것이라 범위 밖에 남은 값은 30 으로 되돌린다.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("villages", sa.Column("village_code", sa.String(length=12), nullable=True))
    op.create_unique_constraint("uq_villages_village_code", "villages", ["village_code"])
    # 같은 리(b_code) 안에서 오래된 마을부터 01, 02 … 연번. 주소 없는 마을은 NULL.
    op.execute(
        """
        UPDATE villages v
           SET village_code = s.code
          FROM (
                SELECT id,
                       b_code || lpad(row_number() OVER (PARTITION BY b_code ORDER BY id)::text, 2, '0')
                         AS code
                  FROM villages
                 WHERE b_code IS NOT NULL AND length(b_code) = 10
               ) s
         WHERE v.id = s.id
        """
    )

    # 토큰이 바뀐 단말이 새 village_id 를 적용하도록 버전을 올린다(기동 시 재발행).
    op.execute("UPDATE current_config SET config_version = config_version + 1")

    op.alter_column("current_config", "file_wait_sec", server_default="30")
    op.execute(
        "UPDATE current_config SET file_wait_sec = 30 WHERE file_wait_sec < 10 OR file_wait_sec > 60"
    )


def downgrade() -> None:
    op.alter_column("current_config", "file_wait_sec", server_default="120")
    op.execute("UPDATE current_config SET file_wait_sec = 120 WHERE file_wait_sec < 30")
    op.drop_constraint("uq_villages_village_code", "villages", type_="unique")
    op.drop_column("villages", "village_code")
    # config_version 은 되돌리지 않는다 — 단조 증가 카운터라 내리면 단말이 낡은 값을
    # 최신으로 오해한다.
