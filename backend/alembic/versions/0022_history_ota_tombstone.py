"""방송 기록 단말별 스냅숏 · 삭제 단말 묘비 · OTA 패키지 (문제점 46·48·50번, 2026-10-04)

Revision ID: 0022
Revises: 0021

broadcast_recipients — 방송을 **걸 때** 대상 범위 안에 있던 단말 전부(보낸 것·오프라인이라 못
  보낸 것)를 박아 둔다. 그래야 방송 기록을 단말별로 적을 수 있고(50번), 오프라인이라 못 받은
  단말을 「응답 없음」이 아니라 「오프라인(미발송)」으로 가를 수 있다. 라벨·마을 이름은 그때
  값의 스냅숏이다 — 나중에 단말을 지우거나 옮겨도 기록은 그대로 읽힌다.
device_tombstones — 지운 단말의 MAC. 공유 계정으로 아직 붙어 있는 단말이 STATUS 를 보내면
  예전엔 미배정 단말로 다시 자동 등록됐다(46번). 묘비가 있으면 서버가 그 MAC 의 메시지를
  버린다. 신규 단말 등록(QR·수동)으로 다시 등록하면 묘비가 지워진다.
ota_packages · ota_tokens · broadcast_events.ota_package_id — OTA(48번). OTA 작업은
  broadcast_events(event_type OTA_START) 한 행이고, 단말 응답(OTA_PROGRESS·OTA_RESULT)은
  방송과 같은 경로로 device_events 에 쌓인다.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broadcast_recipients",
        sa.Column(
            "event_id",
            sa.BigInteger,
            sa.ForeignKey("broadcast_events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mac", sa.String(12), primary_key=True),
        sa.Column("label", sa.String(100)),
        sa.Column("village_id", sa.Integer),
        sa.Column("village_name", sa.String(100)),
        sa.Column("sent", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_broadcast_recipients_mac", "broadcast_recipients", ["mac"])
    op.create_index("ix_broadcast_recipients_village_id", "broadcast_recipients", ["village_id"])

    op.create_table(
        "device_tombstones",
        sa.Column("mac", sa.String(12), primary_key=True),
        sa.Column("label", sa.String(100)),
        sa.Column("village_name", sa.String(100)),
        sa.Column("deleted_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "deleted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "ota_packages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("pkg_version", sa.Integer, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("uploaded_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "ota_tokens",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column(
            "package_id",
            sa.Integer,
            sa.ForeignKey("ota_packages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_id", sa.BigInteger),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_ota_tokens_package_id", "ota_tokens", ["package_id"])
    op.create_index("ix_ota_tokens_expires_at", "ota_tokens", ["expires_at"])

    op.add_column(
        "broadcast_events",
        sa.Column(
            "ota_package_id",
            sa.Integer,
            sa.ForeignKey("ota_packages.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # 기록 조회가 (종류·시각) 순으로 넘긴다.
    op.create_index(
        "ix_broadcast_events_type_triggered",
        "broadcast_events",
        ["event_type", sa.text("triggered_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_broadcast_events_type_triggered", table_name="broadcast_events")
    op.drop_column("broadcast_events", "ota_package_id")
    op.drop_index("ix_ota_tokens_expires_at", table_name="ota_tokens")
    op.drop_index("ix_ota_tokens_package_id", table_name="ota_tokens")
    op.drop_table("ota_tokens")
    op.drop_table("ota_packages")
    op.drop_table("device_tombstones")
    op.drop_index("ix_broadcast_recipients_village_id", table_name="broadcast_recipients")
    op.drop_index("ix_broadcast_recipients_mac", table_name="broadcast_recipients")
    op.drop_table("broadcast_recipients")
