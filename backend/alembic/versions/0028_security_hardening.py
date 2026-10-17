"""공공 보안 보완 — 감사 기록·기록 위변조 방지·재사용 금지·동시 접속·단말 비밀번호 암호화
(문제점 70번 10/15 ①③④⑤, 2026-10-17)

Revision ID: 0028
Revises: 0027

· audit_events: 관리 행위 감사 기록(변경 요청 한 줄씩). app/models/audit.py.
· login_events·audit_events 해시 체인: BEFORE INSERT 트리거가 chain_seq·prev_hash·row_hash 를
  채운다. 한 줄을 고치거나 빼면 그 뒤 해시가 전부 어긋난다(GET /api/audit/verify 가 다시 계산해 본다).
  수정·삭제 방지 트리거: 감사 기록은 수정 불가, 접속기록은 로그아웃 시각·계정 삭제 때의 user_id 만
  바꿀 수 있다. 삭제는 둘 다 1년이 지난 줄(보관 기간 정리)만. TRUNCATE 는 막는다.
  ⚠ DB 소유자 계정은 트리거를 끌 수 있다 — 그래서 월 점검 때 마지막 해시를 감사 기록에 남겨(앵커)
    통째로 다시 계산해 덮어쓴 경우도 드러나게 한다(10 문서).
· users.password_history(직전 비밀번호 해시들) · users.session_key(지금 유효한 세션 하나).
· devices.mqtt_password: 평문 → AES-GCM 암호문(app/core/secretbox.py), 길이 제한 없는 text 로.
  mqtt_password_issued_at 추가 — 기존 단말은 NULL 이라 화면에 다시 보이지 않는다(재발행해야 보임).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_TS = """to_char({col} AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')"""

LOGIN_HASH = f"""
CREATE OR REPLACE FUNCTION xwifi_login_event_hash(e login_events) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT encode(sha256(convert_to(concat_ws('|',
    e.chain_seq::text, {_TS.format(col="e.logged_in_at")}, e.username,
    coalesce(e.ip, ''), coalesce(e.user_agent, ''), e.result, coalesce(e.prev_hash, '')
  ), 'UTF8')), 'hex')
$$
"""

AUDIT_HASH = f"""
CREATE OR REPLACE FUNCTION xwifi_audit_event_hash(e audit_events) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT encode(sha256(convert_to(concat_ws('|',
    e.chain_seq::text, {_TS.format(col="e.at")}, coalesce(e.user_id::text, ''),
    coalesce(e.username, ''), coalesce(e.role, ''), coalesce(e.ip, ''), e.method, e.path, e.action,
    coalesce(e.target, ''), e.status::text, coalesce(e.detail::text, ''), coalesce(e.prev_hash, '')
  ), 'UTF8')), 'hex')
$$
"""


def _chain_trigger(table: str, ts_col: str, hash_fn: str) -> list[str]:
    """asyncpg 는 한 번에 한 문장만 받는다 — 함수와 트리거를 따로 돌려준다."""
    return [f"""
CREATE OR REPLACE FUNCTION xwifi_chain_{table}() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  last_seq bigint;
  last_hash text;
BEGIN
  -- 동시에 들어오는 두 줄이 같은 앞 줄을 잇지 않게 체인 하나를 한 줄씩 잇는다.
  PERFORM pg_advisory_xact_lock(hashtext('xwifi_chain_{table}'));
  SELECT chain_seq, row_hash INTO last_seq, last_hash
    FROM {table} WHERE chain_seq IS NOT NULL ORDER BY chain_seq DESC LIMIT 1;
  IF NEW.{ts_col} IS NULL THEN NEW.{ts_col} := now(); END IF;
  NEW.chain_seq := coalesce(last_seq, 0) + 1;
  NEW.prev_hash := last_hash;
  NEW.row_hash := {hash_fn}(NEW);
  RETURN NEW;
END $$""", f"""CREATE TRIGGER {table}_chain BEFORE INSERT ON {table}
  FOR EACH ROW EXECUTE FUNCTION xwifi_chain_{table}()"""]


GUARD_LOGIN = ["""
CREATE OR REPLACE FUNCTION xwifi_guard_login_events() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION '접속기록(login_events)은 통째로 지울 수 없습니다';
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF OLD.logged_in_at > now() - interval '365 days' THEN
      RAISE EXCEPTION '1년이 안 된 접속기록은 지울 수 없습니다 (id %)', OLD.id;
    END IF;
    RETURN OLD;
  END IF;
  -- 로그아웃 시각과(계정 삭제 때 NULL 이 되는) user_id 만 바꿀 수 있다.
  IF (NEW.id, NEW.username, NEW.ip, NEW.user_agent, NEW.result, NEW.logged_in_at,
      NEW.chain_seq, NEW.prev_hash, NEW.row_hash)
     IS DISTINCT FROM
     (OLD.id, OLD.username, OLD.ip, OLD.user_agent, OLD.result, OLD.logged_in_at,
      OLD.chain_seq, OLD.prev_hash, OLD.row_hash) THEN
    RAISE EXCEPTION '접속기록은 고칠 수 없습니다 (id %)', OLD.id;
  END IF;
  IF OLD.logged_out_at IS NOT NULL AND NEW.logged_out_at IS DISTINCT FROM OLD.logged_out_at THEN
    RAISE EXCEPTION '이미 적힌 로그아웃 시각은 고칠 수 없습니다 (id %)', OLD.id;
  END IF;
  RETURN NEW;
END $$""", """CREATE TRIGGER login_events_guard BEFORE UPDATE OR DELETE ON login_events
  FOR EACH ROW EXECUTE FUNCTION xwifi_guard_login_events()""",
    """CREATE TRIGGER login_events_guard_truncate BEFORE TRUNCATE ON login_events
  FOR EACH STATEMENT EXECUTE FUNCTION xwifi_guard_login_events()"""]

GUARD_AUDIT = ["""
CREATE OR REPLACE FUNCTION xwifi_guard_audit_events() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION '감사 기록(audit_events)은 통째로 지울 수 없습니다';
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF OLD.at > now() - interval '365 days' THEN
      RAISE EXCEPTION '1년이 안 된 감사 기록은 지울 수 없습니다 (id %)', OLD.id;
    END IF;
    RETURN OLD;
  END IF;
  RAISE EXCEPTION '감사 기록은 고칠 수 없습니다 (id %)', OLD.id;
END $$""", """CREATE TRIGGER audit_events_guard BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION xwifi_guard_audit_events()""",
    """CREATE TRIGGER audit_events_guard_truncate BEFORE TRUNCATE ON audit_events
  FOR EACH STATEMENT EXECUTE FUNCTION xwifi_guard_audit_events()"""]

# 기존 접속기록에 체인을 잇는다(보호 트리거를 걸기 전에).
BACKFILL_LOGIN = """
DO $$
DECLARE
  r record;
  prev text := NULL;
  seq bigint := 0;
BEGIN
  FOR r IN SELECT id FROM login_events ORDER BY id LOOP
    seq := seq + 1;
    UPDATE login_events SET chain_seq = seq, prev_hash = prev WHERE id = r.id;
    UPDATE login_events e SET row_hash = xwifi_login_event_hash(e) WHERE e.id = r.id
      RETURNING e.row_hash INTO prev;
  END LOOP;
END $$
"""


def upgrade() -> None:
    # ── 계정 ──
    op.add_column(
        "users",
        sa.Column(
            "password_history", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("users", sa.Column("session_key", sa.String(32), nullable=True))

    # ── 단말 비밀번호 암호화 ──
    op.alter_column("devices", "mqtt_password", type_=sa.Text(), existing_nullable=True)
    op.add_column(
        "devices", sa.Column("mqtt_password_issued_at", sa.DateTime(timezone=True), nullable=True)
    )
    from app.core.secretbox import PREFIX, encrypt

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT mac, mqtt_password FROM devices WHERE mqtt_password IS NOT NULL")
    ).all()
    for mac, pw in rows:
        if not pw.startswith(PREFIX):
            bind.execute(
                sa.text("UPDATE devices SET mqtt_password = :v WHERE mac = :m"),
                {"v": encrypt(pw), "m": mac},
            )

    # ── 감사 기록 ──
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(50), nullable=True),
        sa.Column("role", sa.String(20), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("target", sa.String(100), nullable=True),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("chain_seq", sa.BigInteger(), nullable=True, unique=True),
        sa.Column("prev_hash", sa.String(64), nullable=True),
        sa.Column("row_hash", sa.String(64), nullable=True),
    )
    op.create_index("ix_audit_events_at", "audit_events", ["at"])
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])

    # ── 접속기록 체인 ──
    op.add_column("login_events", sa.Column("chain_seq", sa.BigInteger(), nullable=True))
    op.add_column("login_events", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("login_events", sa.Column("row_hash", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_login_events_chain_seq", "login_events", ["chain_seq"])

    op.execute(LOGIN_HASH)
    op.execute(AUDIT_HASH)
    op.execute(BACKFILL_LOGIN)
    for stmt in (
        *_chain_trigger("login_events", "logged_in_at", "xwifi_login_event_hash"),
        *_chain_trigger("audit_events", "at", "xwifi_audit_event_hash"),
        *GUARD_LOGIN,
        *GUARD_AUDIT,
    ):
        op.execute(stmt)


def downgrade() -> None:
    for t in ("login_events", "audit_events"):
        op.execute(f"DROP TRIGGER IF EXISTS {t}_guard ON {t}")
        op.execute(f"DROP TRIGGER IF EXISTS {t}_guard_truncate ON {t}")
        op.execute(f"DROP TRIGGER IF EXISTS {t}_chain ON {t}")
        op.execute(f"DROP FUNCTION IF EXISTS xwifi_guard_{t}()")
        op.execute(f"DROP FUNCTION IF EXISTS xwifi_chain_{t}()")
    op.drop_constraint("uq_login_events_chain_seq", "login_events", type_="unique")
    op.drop_column("login_events", "row_hash")
    op.drop_column("login_events", "prev_hash")
    op.drop_column("login_events", "chain_seq")
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION IF EXISTS xwifi_login_event_hash(login_events)")
    op.execute("DROP FUNCTION IF EXISTS xwifi_audit_event_hash(audit_events)")
    # 단말 비밀번호는 복호화해 평문으로 되돌린다.
    from app.core.secretbox import decrypt

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT mac, mqtt_password FROM devices WHERE mqtt_password IS NOT NULL")
    ).all()
    for mac, pw in rows:
        bind.execute(
            sa.text("UPDATE devices SET mqtt_password = :v WHERE mac = :m"),
            {"v": decrypt(pw), "m": mac},
        )
    op.drop_column("devices", "mqtt_password_issued_at")
    op.alter_column("devices", "mqtt_password", type_=sa.String(16), existing_nullable=True)
    op.drop_column("users", "session_key")
    op.drop_column("users", "password_history")
