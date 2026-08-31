"""device_events.mac → devices.mac FK 제거.

이력은 불변 로그다 — 단말을 삭제(도난·폐기·재등록)해도 과거 방송 기록은
남아야 한다. FK 가 있으면 이벤트가 하나라도 있는 단말은 삭제가 거부된다
(2026-08-31 운영에서 단말 삭제 500 으로 실제 발생). CASCADE 로 이력을 같이
지우는 방안은 버렸다: 과거 방송의 "몇 대가 응답했나"가 소급해서 바뀐다.

mac 인덱스는 유지한다 — 이력 조회 키다.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("device_events_mac_fkey", "device_events", type_="foreignkey")


def downgrade() -> None:
    # 고아 mac(삭제된 단말의 이력)이 있으면 실패한다 — 그 행들을 지우기 전에는
    # 되돌릴 수 없고, 지우는 것은 이 마이그레이션이 할 일이 아니다.
    op.create_foreign_key(
        "device_events_mac_fkey", "device_events", "devices", ["mac"], ["mac"]
    )
