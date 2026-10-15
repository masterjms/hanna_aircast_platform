#!/usr/bin/env bash
#
# 납품 묶음 설치·업데이트 (문제점 88번) — 묶음(xwifi-<버전>.tar.gz)을 푼 폴더에서 실행. 인터넷 불필요.
#
#   bash scripts/install-offline.sh                 설치·업데이트
#   bash scripts/install-offline.sh --force         방송 중이어도 진행(업데이트)
#   bash scripts/install-offline.sh --self-signed   인증서가 없으면 시험용 자체 서명 인증서를 만든다(단말은 거부함)
#
# 처음: .env 를 만들고(비밀번호·키는 무작위) 멈춘다 → 도메인 등을 고치고 다시 실행.
# 업데이트: 새 묶음을 같은 폴더에 덮어 풀고 같은 명령. .env·infra/certs·infra/secrets·
#   admin-allow.d/local.conf·mosquitto passwd 는 묶음에 없으므로 그대로 남는다.
#
# 순서는 deploy.sh 와 같다 — 방송 확인 → (업데이트면) DB 백업 → 마이그레이션(교체 전) → 교체 → nginx 재시작 → 헬스체크.
# 다른 점은 git pull·빌드 대신 묶음의 이미지를 docker load 한다는 것뿐이다.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

FORCE=0
SELF_SIGNED=0
for arg in "$@"; do
    case "$arg" in
        --force)       FORCE=1 ;;
        --self-signed) SELF_SIGNED=1 ;;
        *) echo "알 수 없는 옵션: $arg"; exit 2 ;;
    esac
done
log() { echo -e "\n\033[1m== $*\033[0m"; }

[ -f VERSION ] && [ -f images.tar.gz ] || { echo "!! 납품 묶음을 푼 폴더에서 실행할 것(VERSION·images.tar.gz 가 없다)"; exit 1; }
VER="$(tr -d '\r\n' < VERSION)"
docker compose version >/dev/null 2>&1 || { echo "!! Docker(compose 플러그인)가 없다 — 먼저 scripts/setup-server.sh"; exit 1; }
echo "xWIFI 납품 묶음 $VER"

# ── 0. 처음이면 .env 를 만들고 멈춘다 ───────────────────────────────
if [ ! -f .env ]; then
    log ".env 만들기 (처음 설치)"
    # head 로 자르면 tr 이 SIGPIPE 로 죽고 pipefail 이 스크립트를 끝낸다 — 먼저 넉넉히 읽고 cut 으로 자른다.
    rnd() { head -c 2048 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-"${1:-32}"; }
    PGPW="$(rnd 24)"
    sed -e "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://xwifi:${PGPW}@postgres:5432/xwifi|" \
        -e "s|^JWT_SECRET=.*|JWT_SECRET=$(rnd 64)|" \
        -e "s|^MQTT_PASSWORD=.*|MQTT_PASSWORD=$(rnd 24)|" \
        -e "s|^ICECAST_SOURCE_PASSWORD=.*|ICECAST_SOURCE_PASSWORD=$(rnd 24)|" \
        -e "s|^ICECAST_ADMIN_PASSWORD=.*|ICECAST_ADMIN_PASSWORD=$(rnd 24)|" \
        -e "s|^GOOGLE_APPLICATION_CREDENTIALS=|# GOOGLE_APPLICATION_CREDENTIALS=|" \
        .env.example > .env
    cat >> .env <<EOF

# ── 납품 묶음 설치(scripts/install-offline.sh 가 만듦) ──
# DB 는 같은 서버의 Postgres 컨테이너(local-db). 사업자 관리형 Postgres 18 을 쓰면 DATABASE_URL 을 그쪽으로
# 바꾸고 COMPOSE_PROFILES 줄을 지운다.
APP_VERSION=$VER
COMPOSE_PROFILES=local-db
POSTGRES_USER=xwifi
POSTGRES_PASSWORD=$PGPW
POSTGRES_DB=xwifi
EOF
    chmod 600 .env
    cat <<'EOF'
   DB·JWT·MQTT·Icecast 비밀번호는 무작위로 채웠다. 아래를 기관 값으로 고친 뒤 같은 명령을 다시 실행:
     PUBLIC_BASE_URL, ICECAST_PUBLIC_BASE_URL   기관 도메인 (https://, 포트 없이)
     KAKAO_REST_API_KEY, KAKAO_JS_KEY           (선택) 지도·주소 검색 — 비우면 지도 없이 동작
     GOOGLE_APPLICATION_CREDENTIALS             (선택) TTS — infra/secrets/google-tts.json 을 넣고 주석을 푼다
     SLACK_WEBHOOK_URL                          (선택) 점검·백업 알림
   인증서: infra/certs/server.crt(전체 체인)·server.key — 설치 가이드 §4
EOF
    exit 0
fi

# .env 는 source 하지 않는다 — 값에 < > 같은 글자가 있으면 셸 문법 오류가 난다. 필요한 키만 읽는다.
envget() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '
'; }
PUBLIC_BASE_URL="$(envget PUBLIC_BASE_URL)"
MQTT_PASSWORD="$(envget MQTT_PASSWORD)"
POSTGRES_USER="$(envget POSTGRES_USER)"
COMPOSE_PROFILES="$(envget COMPOSE_PROFILES)"
export COMPOSE_PROFILES
# 묶음 버전으로 맞춘다 — compose 가 xwifi-backend:<버전> 이미지를 쓰게.
if grep -q '^APP_VERSION=' .env; then
    sed -i "s|^APP_VERSION=.*|APP_VERSION=$VER|" .env
else
    printf '\nAPP_VERSION=%s\n' "$VER" >> .env
fi
export APP_VERSION="$VER"

# ── 1. 인증서 · MQTT 계정 ────────────────────────────────────────────
mkdir -p infra/certs
if [ ! -f infra/certs/server.crt ] || [ ! -f infra/certs/server.key ]; then
    if [ "$SELF_SIGNED" -eq 1 ]; then
        log "시험용 자체 서명 인증서 (단말은 이 인증서를 거부한다 — 화면 확인용)"
        CN="$(printf '%s' "${PUBLIC_BASE_URL:-https://localhost}" | sed -E 's|^https?://||; s|[:/].*||')"
        openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=$CN" \
            -keyout infra/certs/server.key -out infra/certs/server.crt 2>/dev/null
        chmod 644 infra/certs/server.crt; chmod 640 infra/certs/server.key
    else
        echo "!! 인증서가 없다: infra/certs/server.crt · server.key"
        echo "   인터넷에 열린 서버: sudo bash scripts/setup-certs.sh <도메인> <이메일>"
        echo "   기관 인증서: 두 파일을 넣는다(설치 가이드 §4). 화면만 먼저 볼 때: --self-signed"
        exit 1
    fi
fi
# mosquitto 는 키를 uid 1883 으로 읽는다(root 로 실행 중이면 소유자를 맞춘다).
[ "$(id -u)" -eq 0 ] && chown root:1883 infra/certs/server.key 2>/dev/null || true

log "Docker 이미지 불러오기"
if docker image inspect "xwifi-backend:$VER" >/dev/null 2>&1 && docker image inspect "xwifi-frontend:$VER" >/dev/null 2>&1; then
    echo "   $VER 이미지가 이미 있다 — 건너뜀"
else
    gunzip -c images.tar.gz | docker load
fi

if [ ! -f infra/mosquitto/config/passwd ]; then
    log "MQTT 서버 계정 만들기"
    MQTT_SERVER_PASSWORD="$MQTT_PASSWORD" bash scripts/setup-mqtt-users.sh >/dev/null
    echo "   infra/mosquitto/config/passwd (서버 계정 = .env 의 MQTT_PASSWORD, 단말은 단말별 계정)"
fi

# ── 2. 업데이트면 진행 중인 방송 확인 · DB 백업 ──────────────────────
RUNNING=0
docker compose ps --status running --services 2>/dev/null | grep -qx backend && RUNNING=1
if [ "$RUNNING" -eq 1 ]; then
    log "진행 중인 방송 확인"
    ACTIVE="$(docker compose exec -T backend python -c "
import asyncio
from sqlalchemy import func, select
from app.db import engine, session_scope
from app.models.event import BroadcastEvent
async def main():
    async with session_scope() as db:
        n = await db.scalar(select(func.count()).select_from(BroadcastEvent).where(BroadcastEvent.ended_at.is_(None)))
    await engine.dispose()
    print(n)
asyncio.run(main())
" 2>/dev/null | tr -d '\r' | tail -1 || echo "?")"
    if [ "$ACTIVE" != "0" ] && [ "$ACTIVE" != "?" ] && [ "$FORCE" -eq 0 ]; then
        echo "!! 진행 중인 방송(또는 OTA) ${ACTIVE}건 — 교체하면 끊기고 단말은 스스로 복구하지 않는다."
        echo "   끝난 뒤 다시 실행하거나, 알고도 진행하려면 --force"
        exit 1
    fi
    echo "   ${ACTIVE}건"
    if printf '%s' "${COMPOSE_PROFILES:-}" | grep -q local-db; then
        log "DB 백업 (교체 전)"
        bash scripts/backup-db.sh || echo "   !! 백업 실패 — 계속하려면 원인을 확인할 것"
    fi
fi

# ── 3. DB · 마이그레이션 (교체 전) ───────────────────────────────────
if printf '%s' "${COMPOSE_PROFILES:-}" | grep -q local-db; then
    log "DB 기동"
    docker compose up -d --no-build --pull never postgres
    for i in $(seq 1 30); do
        docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-xwifi}" >/dev/null 2>&1 && { echo "   준비됨"; break; }
        sleep 2
        [ "$i" -eq 30 ] && { echo "!! DB 가 뜨지 않는다 — docker compose logs postgres"; exit 1; }
    done
fi

log "DB 마이그레이션"
if ! docker compose run --rm --no-deps backend python -m alembic upgrade head; then
    echo "!! 마이그레이션 실패 — 컨테이너를 교체하지 않고 중단한다(기존 서비스는 그대로)."
    exit 1
fi
# 최고 관리자 계정(멱등) — 처음 만들 때만 비밀번호를 한 번 출력한다.
docker compose run --rm --no-deps backend python -m app.seed

# ── 4. 교체 + nginx 재시작 ──────────────────────────────────────────
log "컨테이너 기동"
docker compose up -d --no-build --pull never
docker compose restart nginx      # 컨테이너가 새 IP 를 받으면 nginx 가 옛 IP 를 붙잡고 502 — deploy.sh 3번

# ── 5. 확인 ─────────────────────────────────────────────────────────
log "헬스체크"
for i in $(seq 1 20); do
    sleep 3
    BODY="$(curl -sk https://localhost/health || true)"
    case "$BODY" in
        *'"status":"ok"'*) echo "   $BODY"; log "설치 완료 ($VER)"; exit 0 ;;
        *'"status":'*)     echo "   ($i) $BODY" ;;
        *)                 echo "   ($i) 응답 없음" ;;
    esac
done
echo "!! 헬스체크가 통과하지 못했다 — docker compose logs backend --tail 50"
echo "   되돌리기: 이전 묶음을 같은 폴더에 다시 풀고 이 스크립트 실행(DB 는 교체 전 백업 ~/db-backups 에서 restore-db.sh)"
exit 1
