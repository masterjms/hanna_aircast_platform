#!/usr/bin/env bash
#
# 서버 내부 점검 → Slack (cron 으로 매일 실행)
#
#   bash scripts/healthcheck.sh          이상이 있을 때만 Slack 으로 보낸다
#   bash scripts/healthcheck.sh --always 정상이어도 한 줄 요약을 보낸다 (매일 cron 에 권장 —
#                                        "조용함 = 정상" 과 "cron 이 안 돌아 조용함" 을 구분한다)
#   bash scripts/healthcheck.sh --dry    화면에만 출력
#
# cron 등록 예 (매일 08:05 KST = 23:05 UTC, EC2 기본 시간대가 UTC 일 때):
#   5 23 * * *  cd ~/xwifi-server && bash scripts/healthcheck.sh --always >> ~/healthcheck.log 2>&1
#
# DB 가 RDS 면(.env DATABASE_URL 호스트가 compose 의 postgres 가 아니면) postgres 컨테이너와
# 로컬 백업 파일은 점검하지 않는다 — 백업은 RDS 자동 백업이 맡는다(docs/current/06 §13).
#
# CloudWatch 가 못 보는 것들을 본다:
#   · 디스크·메모리    에이전트를 깔아야 CloudWatch 로 보이는 항목이다
#   · 컨테이너 상태    호스트는 멀쩡한데 컨테이너만 죽은 경우
#   · 앱 health       DB·MQTT 까지 정상인지
#   · 백업 최신성      "백업이 도는 줄 알았는데 몇 달째 멈춰 있었다" 를 막는다
#   · 인증서 만료      갱신 훅이 조용히 실패하면 90일 뒤 서비스가 멈춘다
#
# 반대로 "서버가 죽었는가" 는 여기서 못 본다 — 서버가 죽으면 이 스크립트도
# 안 돈다. 그건 CloudWatch StatusCheckFailed 가 담당한다.
#
set -uo pipefail          # -e 는 쓰지 않는다. 한 항목이 실패해도 나머지는 점검한다.

cd "$(dirname "${BASH_SOURCE[0]}")/.."

MODE="${1:-}"
DISK_WARN="${DISK_WARN:-80}"        # %
MEM_WARN="${MEM_WARN:-85}"          # %
BACKUP_MAX_AGE_H="${BACKUP_MAX_AGE_H:-30}"   # 시간. 매일 1회면 30시간이면 충분한 여유
CERT_WARN_DAYS="${CERT_WARN_DAYS:-20}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/db-backups}"

PROBLEMS=()
LINES=()
SUMMARY=()               # 정상일 때 한 줄 요약에 넣을 짧은 값들

# DB 가 어디 있나 — compose 의 postgres 컨테이너(local-db 프로파일)인지, RDS 인지.
DB_HOST="$(grep -E '^DATABASE_URL=' .env 2>/dev/null | sed -E 's#.*@([^:/?]+).*#\1#' || true)"
if [ -z "$DB_HOST" ] || [ "$DB_HOST" = "postgres" ] || [ "$DB_HOST" = "localhost" ] || [ "$DB_HOST" = "127.0.0.1" ]; then
    LOCAL_DB=1
else
    LOCAL_DB=0
fi

add_ok()   { LINES+=("  ✅ $1"); }
add_bad()  { LINES+=("  🔴 $1"); PROBLEMS+=("$1"); }
add_warn() { LINES+=("  🟡 $1"); PROBLEMS+=("$1"); }

# ── 디스크 ──────────────────────────────────────────────────────────
DISK_PCT="$(df --output=pcent / | tail -1 | tr -dc '0-9')"
DISK_FREE="$(df -h --output=avail / | tail -1 | tr -d ' ')"
if [ "${DISK_PCT:-0}" -ge "$DISK_WARN" ]; then
    add_bad "디스크 ${DISK_PCT}% 사용 (여유 ${DISK_FREE})"
else
    add_ok "디스크 ${DISK_PCT}% (여유 ${DISK_FREE})"
fi
SUMMARY+=("디스크 ${DISK_PCT:-?}%")

# ── 메모리 ──────────────────────────────────────────────────────────
MEM_PCT="$(free 2>/dev/null | awk '/^Mem:/ {printf "%d", $3/$2*100}')"
if [ -z "$MEM_PCT" ]; then
    add_warn "메모리 사용률을 읽지 못했다"
elif [ "$MEM_PCT" -ge "$MEM_WARN" ]; then
    add_warn "메모리 ${MEM_PCT}% 사용"
else
    add_ok "메모리 ${MEM_PCT}%"
fi
SUMMARY+=("메모리 ${MEM_PCT:-?}%")

# ── 컨테이너 ────────────────────────────────────────────────────────
# 떠 있어야 할 것들. postgres 는 local-db 프로파일일 때만(RDS 면 컨테이너가 없다).
EXPECTED=(xwifi-backend xwifi-nginx xwifi-mosquitto xwifi-icecast)
[ "$LOCAL_DB" = 1 ] && EXPECTED+=(xwifi-postgres)
DOWN=()
for name in "${EXPECTED[@]}"; do
    # tr 로 개행을 턴다 — 실패 경로에서 빈 줄이 섞여 메시지가 깨진다.
    state="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null | tr -d '[:space:]')"
    [ -n "$state" ] || state="없음"
    [ "$state" = "running" ] || DOWN+=("$name($state)")
done
if [ ${#DOWN[@]} -gt 0 ]; then
    add_bad "컨테이너 이상: ${DOWN[*]}"
else
    add_ok "컨테이너 ${#EXPECTED[@]}개 정상"
fi

# ── 앱 health ───────────────────────────────────────────────────────
HEALTH="$(curl -sk --max-time 10 https://localhost/health || echo '')"
case "$HEALTH" in
    *'"status":"ok"'*) add_ok "앱 health ok"; SUMMARY+=("앱 ok") ;;
    *'"status"'*)      add_bad "앱 health: $HEALTH" ;;
    *)                 add_bad "앱 health 응답 없음" ;;
esac

# ── 백업 최신성 ─────────────────────────────────────────────────────
# 로컬 pg_dump(backup-db.sh)가 도는 구성에서만. RDS 는 자동 백업·PITR 이 맡는다.
if [ "$LOCAL_DB" = 1 ]; then
    LATEST="$(ls -t "$BACKUP_DIR"/xwifi-*.sql.gz 2>/dev/null | head -1 || true)"
    if [ -z "$LATEST" ]; then
        add_bad "DB 백업이 하나도 없다"
    else
        AGE_H=$(( ( $(date +%s) - $(stat -c %Y "$LATEST") ) / 3600 ))
        SIZE="$(du -h "$LATEST" | cut -f1)"
        if [ "$AGE_H" -gt "$BACKUP_MAX_AGE_H" ]; then
            add_bad "최근 백업이 ${AGE_H}시간 전 — cron 이 멈췄을 수 있다"
        else
            add_ok "백업 ${AGE_H}시간 전 ($SIZE)"
            SUMMARY+=("백업 ${AGE_H}h 전")
        fi
    fi
else
    add_ok "DB: RDS (${DB_HOST%%.*}…) — 백업은 RDS 자동 백업"
    SUMMARY+=("DB RDS")
fi

# ── 인증서 만료 ─────────────────────────────────────────────────────
CERT=infra/certs/server.crt
if [ -f "$CERT" ]; then
    END="$(openssl x509 -enddate -noout -in "$CERT" 2>/dev/null | cut -d= -f2)"
    if [ -n "$END" ]; then
        DAYS=$(( ( $(date -d "$END" +%s) - $(date +%s) ) / 86400 ))
        if [ "$DAYS" -le "$CERT_WARN_DAYS" ]; then
            add_bad "TLS 인증서 만료까지 ${DAYS}일 — 자동 갱신을 확인할 것"
        else
            add_ok "TLS 인증서 ${DAYS}일 남음"
            SUMMARY+=("인증서 ${DAYS}일")
        fi
    fi
else
    add_warn "TLS 인증서 파일을 찾을 수 없다 ($CERT)"
fi

# ── 출력 ────────────────────────────────────────────────────────────
if [ ${#PROBLEMS[@]} -gt 0 ]; then
    HEAD="🔴 *서버 점검* — 확인 필요 ${#PROBLEMS[@]}건"
    TEXT="$HEAD"$'\n'"$(printf '%s\n' "${LINES[@]}")"
else
    # 정상은 한 줄로 — 매일 오는 글이 길면 안 읽게 된다. 세부는 --dry 로 본다.
    HEAD="✅ *서버 점검* — 모두 정상"
    TEXT="$HEAD"
    for item in "${SUMMARY[@]}"; do TEXT+=" · $item"; done
    [ "$MODE" = "--dry" ] && TEXT="$TEXT"$'\n'"$(printf '%s\n' "${LINES[@]}")"
fi

if [ "$MODE" = "--dry" ]; then
    echo "$TEXT"
    exit 0
fi

# 정상일 때는 조용히 넘어간다 — 매일 "정상" 알림이 오면 사람이 안 읽게 된다.
# 일일 요약이 필요하면 --always 로 부른다.
if [ ${#PROBLEMS[@]} -eq 0 ] && [ "$MODE" != "--always" ]; then
    echo "정상 — 알림 생략"
    exit 0
fi

WEBHOOK="$(grep -E '^SLACK_WEBHOOK_URL=' .env | cut -d= -f2- | tr -d '"'"'"'' || true)"
[ -n "$WEBHOOK" ] || { echo "!! .env 에 SLACK_WEBHOOK_URL 이 없다"; echo "$TEXT"; exit 1; }

curl -sS -X POST -H 'Content-type: application/json' \
     --data "$(python3 -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))' <<< "$TEXT")" \
     "$WEBHOOK" >/dev/null

echo "전송 완료 (문제 ${#PROBLEMS[@]}건)"
[ ${#PROBLEMS[@]} -eq 0 ]
