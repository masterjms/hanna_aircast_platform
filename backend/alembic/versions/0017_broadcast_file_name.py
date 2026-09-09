"""방송한 파일을 지울 수 있게 — 이력에 파일명을 박고 FK 를 SET NULL 로.

한 번이라도 방송한 파일은 파일함에서 영영 지워지지 않았다. `broadcast_events.file_id`
가 제약 없는 FK 라 참조하는 이력이 있으면 DB 가 삭제를 거부했기 때문이다. 화면에는
「스케줄이나 이력에서 사용 중인 파일은 삭제할 수 없습니다」만 떠서, 스케줄에 넣은 적
없는 사람은 이유를 알 수 없었다.

같은 부류의 세 번째다: 0006 에서 device_events.mac FK 를 뺐고(이력 있는 단말 삭제가
500), 0014 에서 users 를 가리키는 FK 셋을 SET NULL 로 바꿨다(파일을 올렸거나 방송한
계정이 삭제 거부). 원칙은 같다 — **이력은 남되 참조당하는 쪽의 삭제를 막지 않는다.**

그냥 SET NULL 만 하면 이력 화면의 「무엇을」 칸이 빈칸이 된다. 지금은 조회할 때마다
files 에서 이름을 찾아오기 때문이다. 그래서 방송 시작 시점의 파일명을 이력에 박아둔다
(expected_count·bytes_estimated 와 같은 방식). 파일을 지워도 「안내.mp3」는 남고 링크만
끊긴다. 기존 행은 지금 파일명으로 채운다.

schedules.file_id 는 그대로 둔다. 파일이 사라진 스케줄은 매번 조용히 실패하므로 막는
쪽이 맞다 — 대신 어느 스케줄이 쓰고 있는지 서비스가 이름을 대준다.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_FK = "broadcast_events_file_id_fkey"


def upgrade() -> None:
    op.add_column(
        "broadcast_events", sa.Column("file_name", sa.String(length=255), nullable=True)
    )
    # 기존 이력은 아직 파일이 살아 있으므로 지금 이름을 옮겨 담는다.
    op.execute(
        """
        UPDATE broadcast_events e
           SET file_name = f.filename
          FROM files f
         WHERE e.file_id = f.id
           AND e.file_name IS NULL
        """
    )

    op.drop_constraint(_FK, "broadcast_events", type_="foreignkey")
    op.create_foreign_key(
        _FK, "broadcast_events", "files", ["file_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    # 되돌리기 전에 file_id 가 NULL 인데 이름만 남은 행이 있으면 옛 제약과 무관하다
    # (NULL 은 FK 검사를 타지 않는다). 그대로 되돌린다.
    op.drop_constraint(_FK, "broadcast_events", type_="foreignkey")
    op.create_foreign_key(_FK, "broadcast_events", "files", ["file_id"], ["id"])
    op.drop_column("broadcast_events", "file_name")
