#!/usr/bin/env bash
#
# DB 복구
#
#   bash scripts/restore-db.sh                      가장 최근 백업으로
#   bash scripts/restore-db.sh <백업파일>            지정한 파일로
#   bash scripts/restore-db.sh --list               백업 목록만 보기
#
# ⚠ 현재 DB 를 덮어쓴다. 확인을 받고 진행한다.
#
# 복구 전에 현재 상태를 한 번 더 떠둔다 — 복구 자체가 잘못됐을 때
# 되돌아갈 자리가 없으면 상황이 더 나빠진다.
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BACKUP_DIR="${BACKUP_DIR:-$HOME/db-backups}"
SERVICE="${DB_SERVICE:-postgres}"

[ -f .env ] || { echo "!! .env 가 없다"; exit 1; }
DB_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2- | tr -d '"'"'"'' || true)"
DB_NAME="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2- | tr -d '"'"'"'' || true)"
DB_USER="${DB_USER:-xwifi}"
DB_NAME="${DB_NAME:-xwifi}"

if [ "${1:-}" = "--list" ]; then
    echo "== $BACKUP_DIR"
    ls -lh "$BACKUP_DIR"/xwifi-*.sql.gz 2>/dev/null || echo "   (백업 없음)"
    exit 0
fi

FILE="${1:-}"
if [ -z "$FILE" ]; then
    FILE="$(ls -t "$BACKUP_DIR"/xwifi-*.sql.gz 2>/dev/null | head -1 || true)"
    [ -n "$FILE" ] || { echo "!! 백업이 없다: $BACKUP_DIR"; exit 1; }
fi
[ -f "$FILE" ] || { echo "!! 파일이 없다: $FILE"; exit 1; }

echo "== 복구할 백업: $FILE ($(du -h "$FILE" | cut -f1), $(date -r "$FILE" '+%Y-%m-%d %H:%M'))"
echo "== 대상 DB   : $DB_NAME"
echo
echo "⚠ 현재 DB 의 내용이 이 백업 시점으로 완전히 대체된다."
read -rp "  진행하려면 yes 입력: " ans
[ "$ans" = "yes" ] || { echo "취소했다."; exit 1; }

# ── 1. 현재 상태를 먼저 떠둔다 ──────────────────────────────────────
SAFETY="$BACKUP_DIR/before-restore-$(date +%Y%m%d-%H%M%S).sql.gz"
mkdir -p "$BACKUP_DIR"
echo
echo "== 복구 전 현재 상태 저장"
docker compose exec -T "$SERVICE" \
    pg_dump -U "$DB_USER" -d "$DB_NAME" --clean --if-exists 2>/dev/null \
    | gzip > "$SAFETY" || echo "   (현재 DB 를 뜨지 못했다 — 비어 있거나 접속 불가)"
echo "   $SAFETY"

# ── 2. 백엔드를 멈춘다 ──────────────────────────────────────────────
# 복구 중에 앱이 쓰기를 시도하면 충돌한다.
echo
echo "== 백엔드 중지"
docker compose stop backend >/dev/null 2>&1 || true

# ── 3. 복구 ─────────────────────────────────────────────────────────
echo "== 복구 실행"
if zcat "$FILE" | docker compose exec -T "$SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -q; then
    echo "   완료"
else
    echo "!! 복구 중 오류. 되돌리려면:"
    echo "   bash scripts/restore-db.sh $SAFETY"
    docker compose start backend >/dev/null 2>&1 || true
    exit 1
fi

# ── 4. 확인 ─────────────────────────────────────────────────────────
echo
echo "== 복구 결과"
docker compose exec -T "$SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -tAc "
SELECT '   마을 ' || (SELECT count(*) FROM villages) ||
       ' / 단말 ' || (SELECT count(*) FROM devices) ||
       ' / 계정 ' || (SELECT count(*) FROM users) ||
       ' / 방송이력 ' || (SELECT count(*) FROM broadcast_events);
" 2>/dev/null || echo "   (건수 확인 실패 — 직접 확인 필요)"

echo
echo "== 백엔드 재시작"
docker compose start backend >/dev/null 2>&1 || true
sleep 5
docker compose restart nginx >/dev/null 2>&1 || true

echo
echo "확인:  curl -sk https://localhost/health"
