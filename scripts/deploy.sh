#!/usr/bin/env bash
#
# 운영 배포 (서버에서 실행)
#
#   bash scripts/deploy.sh              평소 배포
#   bash scripts/deploy.sh --force      방송 중이어도 진행
#   bash scripts/deploy.sh --no-pull    현재 코드 그대로 재빌드만
#
# 순서에 이유가 있다:
#
#   1. 방송 중이면 멈춘다
#        배포는 컨테이너를 내렸다 올리므로 진행 중인 방송이 끊긴다. 단말은
#        스트림이 끊겨도 재접속하지 않으므로(ESP32 정정 260824) 그 방송은
#        영영 끝난다. 사람이 판단해야 할 일이라 기본은 거절이다.
#
#   2. 마이그레이션을 컨테이너 교체 *전에* 돌린다
#        실패하면 기존 컨테이너가 그대로 살아 있어 서비스가 안 죽는다.
#        교체부터 하면 새 코드가 옛 스키마 위에서 돌아가는 구간이 생긴다.
#
#   3. nginx 를 반드시 함께 재시작한다
#        컨테이너를 재생성하면 새 IP 를 받는데 nginx 는 upstream 호스트명을
#        기동 시 한 번만 해석해 캐시한다. 이걸 빼먹으면 502 가 난다.
#        (2026-08-26 실제로 겪음)
#
#   4. 헬스체크로 확인하고, 실패하면 되돌릴 방법을 알려준다
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

FORCE=0
PULL=1
for arg in "$@"; do
    case "$arg" in
        --force)   FORCE=1 ;;
        --no-pull) PULL=0 ;;
        *) echo "알 수 없는 옵션: $arg"; exit 2 ;;
    esac
done

log() { echo -e "\n\033[1m== $*\033[0m"; }

[ -f .env ] || { echo "!! .env 가 없다. 배포 전에 만들어야 한다."; exit 1; }

PREV_SHA="$(git rev-parse --short HEAD)"

# ── 1. 진행 중인 방송 확인 ──────────────────────────────────────────
log "진행 중인 방송 확인"
ACTIVE="$(docker compose exec -T backend python -c "
import asyncio
from sqlalchemy import func, select
from app.db import engine, session_scope
from app.models.event import BroadcastEvent

async def main():
    async with session_scope() as db:
        n = await db.scalar(
            select(func.count()).select_from(BroadcastEvent)
            .where(BroadcastEvent.ended_at.is_(None))
        )
    await engine.dispose()
    print(n or 0)

asyncio.run(main())
" 2>/dev/null | tr -d '\r' | tail -1 || echo "?")"

if [ "$ACTIVE" = "?" ]; then
    echo "   확인 실패(백엔드가 안 떠 있을 수 있음) — 계속 진행한다"
elif [ "$ACTIVE" != "0" ]; then
    echo "   진행 중인 방송 ${ACTIVE}건"
    if [ "$FORCE" -eq 0 ]; then
        echo "!! 배포하면 이 방송들이 끊기고, 단말은 스스로 복구하지 않는다."
        echo "   방송이 끝난 뒤 다시 실행하거나, 알고도 진행하려면 --force 를 붙일 것."
        exit 1
    fi
    echo "   --force 지정됨 — 방송을 끊고 진행한다"
else
    echo "   없음"
fi

# ── 2. 코드 갱신 ────────────────────────────────────────────────────
if [ "$PULL" -eq 1 ]; then
    log "코드 갱신"
    git pull --ff-only
fi
NEW_SHA="$(git rev-parse --short HEAD)"
echo "   $PREV_SHA → $NEW_SHA"

# ── 3. 빌드 ─────────────────────────────────────────────────────────
log "이미지 빌드"
docker compose build

# ── 4. 마이그레이션 (컨테이너 교체 전) ──────────────────────────────
log "DB 마이그레이션"
if ! docker compose run --rm --no-deps backend python -m alembic upgrade head; then
    echo "!! 마이그레이션 실패 — 컨테이너를 교체하지 않고 중단한다."
    echo "   현재 서비스는 이전 버전으로 계속 돌고 있다."
    exit 1
fi

# ── 5. 교체 + nginx 재시작 ──────────────────────────────────────────
log "컨테이너 교체"
docker compose up -d
docker compose restart nginx      # 위 3번 참고 — 빼면 502

# ── 6. 확인 ─────────────────────────────────────────────────────────
log "헬스체크"
OK=0
for i in $(seq 1 15); do
    sleep 2
    BODY="$(curl -sk https://localhost/health || true)"
    case "$BODY" in
        *'"status":"ok"'*)   echo "   $BODY"; OK=1; break ;;
        *'"status":'*)       echo "   ($i) $BODY" ;;
        *)                   echo "   ($i) 응답 없음" ;;
    esac
done

if [ "$OK" -eq 1 ]; then
    log "배포 완료 ($NEW_SHA)"
    exit 0
fi

echo
echo "!! 헬스체크가 통과하지 못했다."
echo "   로그:      docker compose logs backend --tail 50"
echo "   되돌리기:  git checkout $PREV_SHA && bash scripts/deploy.sh --no-pull"
exit 1
