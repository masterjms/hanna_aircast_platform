"""초기 스키마 — 12 테이블 + job_id 시퀀스

Revision ID: 0001
Revises:
Create Date: 2026-08-20

autogenerate 가 아니라 손으로 썼다. DB 스키마 문서(xWIFI_DB_스키마_260815.md)의 DDL 을
기준으로 하고, 아래 3가지만 구현하면서 추가했다:

  1. files.storage_path / duration_sec / tts_voice   파일 저장을 로컬 디스크로 정한 결과
  2. download_tokens 테이블                          MQTT 1024B 제약 때문에 짧은 토큰이 필요
  3. device_events.dedup_key + UNIQUE 인덱스         QoS 1 중복 수신 방어
  4. broadcast_events.ended_at                       동시 방송 겹침 검사용
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)
_NOW = sa.text("now()")


def upgrade() -> None:
    # LIVE_START·FILE_START·OTA_START 가 공유하는 단일 id 공간.
    # 프로세스 메모리 카운터를 쓰면 재기동 때 되감겨 멱등 처리가 깨진다.
    op.execute("CREATE SEQUENCE IF NOT EXISTS job_id_seq START 1")

    # ── 조직 · 권한 ─────────────────────────────────────────────────────
    op.create_table(
        "villages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("sido", sa.String(50)),
        sa.Column("sigungu", sa.String(50)),
        sa.Column("address_detail", sa.String(255)),
        sa.Column("lat", sa.Float),
        sa.Column("lng", sa.Float),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
    )

    op.create_table(
        "zones",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "village_id",
            sa.Integer,
            sa.ForeignKey("villages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("address_detail", sa.String(255)),
        sa.Column("lat", sa.Float),
        sa.Column("lng", sa.Float),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
    )
    op.create_index("ix_zones_village_id", "zones", ["village_id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(50), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
        sa.CheckConstraint(
            "role IN ('super_admin', 'village_admin')", name="ck_users_role"
        ),
    )

    op.create_table(
        "user_villages",
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "village_id",
            sa.Integer,
            sa.ForeignKey("villages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # ── 단말 ────────────────────────────────────────────────────────────
    op.create_table(
        "devices",
        sa.Column("mac", sa.String(12), primary_key=True),
        sa.Column("label", sa.String(100)),
        # 마을이 지워져도 단말은 남긴다 — 물건은 현장에 그대로 있다.
        sa.Column("village_id", sa.Integer, sa.ForeignKey("villages.id", ondelete="SET NULL")),
        sa.Column("zone_id", sa.Integer, sa.ForeignKey("zones.id", ondelete="SET NULL")),
        sa.Column("firmware_version", sa.String(50)),
        sa.Column("last_status", postgresql.JSONB),
        sa.Column("last_seen_at", _TS),
        sa.Column("registered_at", _TS, nullable=False, server_default=_NOW),
    )
    op.create_index("ix_devices_village_id", "devices", ["village_id"])
    op.create_index("ix_devices_zone_id", "devices", ["zone_id"])
    op.create_index("ix_devices_last_seen_at", "devices", ["last_seen_at"])

    # ── 파일 ────────────────────────────────────────────────────────────
    op.create_table(
        "files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="upload"),
        # FILE_ROOT 기준 상대 경로. 절대 경로를 넣으면 온프레미스 이전 때 깨진다.
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("duration_sec", sa.Numeric(8, 2)),
        sa.Column("tts_text", sa.Text),
        sa.Column("tts_lang", sa.String(10)),
        sa.Column("tts_voice", sa.String(50)),
        sa.Column("uploaded_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
        sa.CheckConstraint("source IN ('upload', 'tts')", name="ck_files_source"),
    )

    op.create_table(
        "download_tokens",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column(
            "file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("job_id", sa.BigInteger),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
    )
    op.create_index("ix_download_tokens_file_id", "download_tokens", ["file_id"])
    op.create_index("ix_download_tokens_expires_at", "download_tokens", ["expires_at"])

    # ── 스케줄 ──────────────────────────────────────────────────────────
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("months", postgresql.ARRAY(sa.Integer), nullable=False),
        sa.Column("weekdays", postgresql.ARRAY(sa.Integer), nullable=False),
        sa.Column("times", postgresql.ARRAY(sa.Time), nullable=False),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("files.id"), nullable=False),
        sa.Column("target_scope", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(50)),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
        sa.CheckConstraint(
            "target_scope IN ('device', 'zone', 'village', 'all')",
            name="ck_schedules_target_scope",
        ),
    )

    # ── 이력 ────────────────────────────────────────────────────────────
    op.create_table(
        "broadcast_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("job_id", sa.BigInteger),
        sa.Column("target_scope", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(50)),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("files.id")),
        sa.Column("schedule_id", sa.Integer, sa.ForeignKey("schedules.id")),
        sa.Column("triggered_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("triggered_at", _TS, nullable=False, server_default=_NOW),
        # NULL = 진행 중. 동시 방송 겹침 검사가 이 조건을 본다.
        sa.Column("ended_at", _TS),
        sa.CheckConstraint(
            "target_scope IN ('device', 'zone', 'village', 'all')",
            name="ck_broadcast_events_scope",
        ),
    )
    op.create_index("ix_broadcast_events_job_id", "broadcast_events", ["job_id"])
    op.create_index("ix_broadcast_events_triggered_at", "broadcast_events", ["triggered_at"])
    op.create_index(
        "ix_broadcast_events_active",
        "broadcast_events",
        ["ended_at"],
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    op.create_table(
        "device_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.BigInteger,
            sa.ForeignKey("broadcast_events.id", ondelete="CASCADE"),
        ),
        sa.Column("mac", sa.String(12), sa.ForeignKey("devices.mac"), nullable=False),
        sa.Column("result_type", sa.String(20)),
        # MQTT 원본 그대로. 단말 프로토콜에 필드가 늘어도 스키마를 안 바꾼다.
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("received_at", _TS, nullable=False, server_default=_NOW),
        sa.Column("dedup_key", sa.String(140)),
    )
    op.create_index("ix_device_events_event_id", "device_events", ["event_id"])
    op.create_index("ix_device_events_mac", "device_events", ["mac"])
    op.create_index("ix_device_events_received_at", "device_events", ["received_at"])
    # QoS 1 은 같은 메시지를 두 번 줄 수 있다. job_id 를 뽑을 수 있는 메시지만 막는다.
    op.create_index(
        "uq_device_events_dedup_key",
        "device_events",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )

    # ── 시스템 ──────────────────────────────────────────────────────────
    op.create_table(
        "current_config",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False, server_default="1"),
        sa.Column("config_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status_interval_sec", sa.Integer, nullable=False, server_default="30"),
        sa.Column("live_stats_interval_sec", sa.Integer, nullable=False, server_default="10"),
        sa.Column("event_qos", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("updated_at", _TS, nullable=False, server_default=_NOW),
        sa.CheckConstraint("id = 1", name="ck_current_config_singleton"),
    )
    op.execute("INSERT INTO current_config (id) VALUES (1) ON CONFLICT DO NOTHING")

    op.create_table(
        "daily_cost_summary",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("summary_date", sa.Date, nullable=False),
        # NULL = 전체(계정 단위) 행. 여기에만 AWS 실비용이 들어간다.
        sa.Column("village_id", sa.Integer, sa.ForeignKey("villages.id", ondelete="CASCADE")),
        sa.Column("broadcast_minutes", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("device_broadcast_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("estimated_egress_mb", sa.Numeric(12, 2)),
        sa.Column("estimated_cost_krw", sa.Numeric(10, 2)),
        sa.Column("actual_total_cost_krw", sa.Numeric(10, 2)),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
    )
    op.create_index("ix_daily_cost_summary_date", "daily_cost_summary", ["summary_date"])
    # NULLS NOT DISTINCT (PG15+) — 없으면 village_id IS NULL 행이 하루에 여러 개 생긴다.
    op.execute(
        "CREATE UNIQUE INDEX uq_daily_cost_summary_date_village "
        "ON daily_cost_summary (summary_date, village_id) NULLS NOT DISTINCT"
    )


def downgrade() -> None:
    op.drop_table("daily_cost_summary")
    op.drop_table("current_config")
    op.drop_table("device_events")
    op.drop_table("broadcast_events")
    op.drop_table("schedules")
    op.drop_table("download_tokens")
    op.drop_table("files")
    op.drop_table("devices")
    op.drop_table("user_villages")
    op.drop_table("users")
    op.drop_table("zones")
    op.drop_table("villages")
    op.execute("DROP SEQUENCE IF EXISTS job_id_seq")
