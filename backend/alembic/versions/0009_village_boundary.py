"""villages.boundary 추가 — 마을 경계 폴리곤.

「구역의 도형」(행정안전부 주소정보제공, 공공누리 제1유형)의 리 경계를 b_code 로
조인해 넣는다. 원본 SHP 는 EPSG:5179(UTM-K)라 WGS84 로 재투영한 GeoJSON geometry
를 그대로 저장한다 — 지도 SDK 가 위경도를 받기 때문이다.

PostGIS 를 쓰지 않는 이유: 그리기만 하고 "이 점이 어느 마을인가" 같은 공간 질의는
하지 않는다. 확장이 필요해지면 그때 geometry 타입으로 옮긴다.

경계 데이터를 리포에 커밋하지 않고 DB 에 두는 이유는 지도 설계 §4.8 참고.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("villages", sa.Column("boundary", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("villages", "boundary")
