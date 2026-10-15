#!/usr/bin/env bash
#
# 납품 묶음 만들기 (문제점 88번) — 인터넷이 막힌 기관 서버에도 설치할 수 있는 파일 하나.
#
#   bash scripts/package-release.sh            버전 = git 태그 또는 커밋
#   bash scripts/package-release.sh 1.0.0      버전 직접
#
# 결과: dist/xwifi-<버전>.tar.gz  (+ .sha256)
#   xwifi-<버전>/
#     images.tar.gz          쓰는 Docker 이미지 전부(backend·frontend 빌드본 + postgres·mosquitto·icecast·nginx)
#     docker-compose.yml     실행 구성
#     .env.example           설정 견본(.env.prod.example)
#     infra/ scripts/        브로커·nginx·icecast 설정, 설치·인증서·백업·점검 스크립트
#     VERSION IMAGES         버전, 이미지 목록
#     설치_가이드.md          docs/current/10 사본
#
# 이 PC(빌드용)는 인터넷이 되어야 한다(이미지 받기·빌드). 설치하는 쪽은 인터넷이 없어도 된다.
# 설치·업데이트: 묶음을 풀고 scripts/install-offline.sh (docs/current/10).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

VER="${1:-$(git describe --tags --always --dirty 2>/dev/null || echo dev)}"
NAME="xwifi-$VER"
OUT="dist/$NAME"
log() { echo -e "\n\033[1m== $*\033[0m"; }
docker info >/dev/null 2>&1 || { echo "!! Docker 가 꺼져 있다 — Docker 를 켠 뒤 다시"; exit 1; }

[ -z "$(git status --porcelain -- backend frontend docker-compose.yml infra scripts)" ] \
    || echo "!! 커밋 안 된 변경이 있다 — 묶음의 코드는 지금 작업 폴더 기준이다."

export APP_VERSION="$VER"
# compose 는 파일 전체를 먼저 해석한다 — 빌드 PC 에 .env 가 없어도 config·build 가 되게 필수값만 채운다.
# (묶음에는 이 값이 들어가지 않는다. 실제 값은 설치하는 서버의 .env)
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://build@postgres/build}"
export ICECAST_SOURCE_PASSWORD="${ICECAST_SOURCE_PASSWORD:-build}" ICECAST_ADMIN_PASSWORD="${ICECAST_ADMIN_PASSWORD:-build}"
[ -f .env ] || { touch .env; TOUCHED_ENV=1; }
trap '[ "${TOUCHED_ENV:-0}" = 1 ] && rm -f .env' EXIT
PROFILES=(--profile local-db)

log "backend·frontend 빌드 ($VER)"
docker compose build backend frontend

log "외부 이미지 받기"
mapfile -t IMAGES < <(docker compose "${PROFILES[@]}" config --images | tr -d '\r' | sort -u)
for img in "${IMAGES[@]}"; do
    case "$img" in xwifi-*) ;; *) docker pull -q "$img" >/dev/null && echo "   $img" ;; esac
done

log "묶음 만들기 → $OUT"
rm -rf "$OUT" && mkdir -p "$OUT"
docker save "${IMAGES[@]}" | gzip -1 > "$OUT/images.tar.gz"
# 실행에 필요한 파일만(소스·문서·시험 도구는 빼고). .gitignore 에 걸린 것(.env·인증서·비밀번호·TTS 키)은 들어가지 않는다.
git ls-files -z --cached --others --exclude-standard -- docker-compose.yml infra 'scripts/*.sh' \
    | tar --null -T - -cf - | tar -x -C "$OUT"
cp .env.prod.example "$OUT/.env.example"
cp "docs/current/10_설치_인계_가이드.md" "$OUT/설치_가이드.md"
mkdir -p "$OUT/infra/certs"
echo "$VER" > "$OUT/VERSION"
printf '%s\n' "${IMAGES[@]}" > "$OUT/IMAGES"

tar -C dist -czf "dist/$NAME.tar.gz" "$NAME"
( cd dist && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" )
rm -rf "$OUT"
log "완료: dist/$NAME.tar.gz ($(du -h "dist/$NAME.tar.gz" | cut -f1))"
cat "dist/$NAME.tar.gz.sha256"
