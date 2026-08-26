#!/usr/bin/env bash
#
# DB 백업 (매일 자동 실행)
#
#   bash scripts/backup-db.sh
#
# 하는 일:
#   1. pg_dump 로 DB 전체를 파일 하나로 뜬다
#   2. 압축해서 로컬에 보관 (기본 7일)
#   3. S3 가 설정돼 있으면 업로드
#
# 왜 EBS 스냅샷으로 안 되나:
#   돌아가는 Postgres 의 디스크 스냅샷은 전원을 뽑은 것과 같다(crash-consistent).
#   대개 복구되지만 보장이 없다. pg_dump 는 트랜잭션 일관성이 보장된 시점을 뜬다.
#   EBS 스냅샷은 "방송 파일" 용이고, DB 는 이 스크립트가 담당한다.
#
# 왜 로컬에도 남기고 S3 에도 올리나:
#   로컬만 두면 EC2 가 죽을 때 백업도 같이 죽는다 — 백업의 의미가 없다.
#   S3 만 두면 네트워크 문제일 때 복구가 막힌다. 둘 다 둔다.
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BACKUP_DIR="${BACKUP_DIR:-$HOME/db-backups}"
KEEP_DAYS="${KEEP_DAYS:-7}"
S3_BUCKET="${S3_BUCKET:-}"          # 예: s3://hanna-aircast-backup/db
SERVICE="${DB_SERVICE:-postgres}"   # compose 서비스 이름

[ -f .env ] || { echo "!! .env 가 없다"; exit 1; }

# .env 에서 DB 접속 정보를 읽는다(따옴표·주석 무시).
DB_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2- | tr -d '"'"'"'' || true)"
DB_NAME="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2- | tr -d '"'"'"'' || true)"
DB_USER="${DB_USER:-xwifi}"
DB_NAME="${DB_NAME:-xwifi}"

STAMP="$(date +%Y%m%d-%H%M%S)"
FILE="$BACKUP_DIR/xwifi-$STAMP.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "== 백업 시작 ($DB_NAME)"

# --clean --if-exists: 복구할 때 기존 객체를 지우고 새로 만든다.
#                     이게 없으면 "이미 있다" 오류로 복구가 중간에 멈춘다.
if ! docker compose exec -T "$SERVICE" \
        pg_dump -U "$DB_USER" -d "$DB_NAME" --clean --if-exists \
     | gzip > "$FILE"; then
    echo "!! pg_dump 실패"
    rm -f "$FILE"
    exit 1
fi

SIZE="$(du -h "$FILE" | cut -f1)"

# 빈 파일이 만들어지는 사고를 잡는다. 파이프라인이라 pg_dump 가 실패해도
# gzip 은 성공할 수 있어서, 크기를 직접 확인한다.
BYTES="$(stat -c %s "$FILE")"
if [ "$BYTES" -lt 1000 ]; then
    echo "!! 백업 파일이 너무 작다(${BYTES}B) — 덤프가 제대로 안 됐다"
    rm -f "$FILE"
    exit 1
fi

# 내용도 확인한다. 테이블 생성 구문이 없으면 껍데기다.
if ! zcat "$FILE" | grep -q "CREATE TABLE"; then
    echo "!! 백업에 테이블 정의가 없다 — 덤프 실패로 본다"
    rm -f "$FILE"
    exit 1
fi

echo "   $FILE ($SIZE)"

# ── S3 업로드 ───────────────────────────────────────────────────────
if [ -n "$S3_BUCKET" ]; then
    if command -v aws >/dev/null; then
        aws s3 cp "$FILE" "$S3_BUCKET/" --only-show-errors
        echo "   S3 업로드 완료: $S3_BUCKET/$(basename "$FILE")"
    else
        echo "   !! aws CLI 가 없어 S3 업로드를 건너뛴다"
    fi
else
    echo "   (S3_BUCKET 미설정 — 로컬에만 보관)"
fi

# ── 오래된 백업 정리 ────────────────────────────────────────────────
find "$BACKUP_DIR" -name 'xwifi-*.sql.gz' -mtime "+$KEEP_DAYS" -delete
COUNT="$(find "$BACKUP_DIR" -name 'xwifi-*.sql.gz' | wc -l)"
echo "== 완료 (로컬 보관 ${COUNT}개, ${KEEP_DAYS}일 보존)"
