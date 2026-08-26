#!/usr/bin/env bash
#
# Google TTS 자격증명을 컨테이너가 읽을 수 있는 자리로 옮긴다 (운영 서버에서 실행)
#
#   sudo bash scripts/setup-tts-credentials.sh
#
# 전제: 먼저 아래를 끝내둔다.
#   gcloud auth application-default login --no-launch-browser
#   gcloud auth application-default set-quota-project <프로젝트ID>
#
# 왜 복사가 필요한가:
#   gcloud 는 자격증명을 ~/.config/gcloud 에 0600 ubuntu 소유로 만든다.
#   백엔드 컨테이너는 uid 10001(xwifi)로 도므로 그대로는 읽지 못한다.
#   컨테이너 사용자 소유로 복사본을 두고 0600 을 유지한다.
#
# ⚠ 이 파일은 개인 계정의 리프레시 토큰이다. 서비스 계정 키 생성이 조직 정책으로
#   막혀 있어 임시로 쓰는 방식이며, 운영 안정화 후에는 Workload Identity 연동으로
#   옮기는 것이 맞다(키 파일 자체가 없어진다).
#
set -euo pipefail

# 컨테이너 안 백엔드 사용자. backend/Dockerfile 의 uid 와 반드시 같아야 한다.
APP_UID=10001

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="$REPO_DIR/infra/secrets"
DEST="$DEST_DIR/google-tts.json"

# sudo 로 실행돼도 원래 사용자의 홈을 봐야 한다.
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
SRC="${GOOGLE_ADC_PATH:-$REAL_HOME/.config/gcloud/application_default_credentials.json}"

if [ ! -f "$SRC" ]; then
    echo "!! 자격증명이 없다: $SRC"
    echo "   먼저 실행할 것:"
    echo "     gcloud auth application-default login --no-launch-browser"
    exit 1
fi

# quota project 가 없으면 TTS 호출이 "API not enabled" 로 실패한다.
# 로그인 직후 경고로 나오는 그 항목이다.
if ! grep -q '"quota_project_id"' "$SRC"; then
    echo "!! quota project 가 설정돼 있지 않다. TTS 호출이 실패한다."
    echo "   먼저 실행할 것:"
    echo "     gcloud auth application-default set-quota-project <프로젝트ID>"
    exit 1
fi

mkdir -p "$DEST_DIR"
cp "$SRC" "$DEST"
chown "$APP_UID:$APP_UID" "$DEST"
chmod 600 "$DEST"

echo "== 복사 완료: $DEST (uid $APP_UID 전용)"
echo
echo "   .env 에 다음이 들어 있어야 한다:"
echo "     TTS_ENGINE=google"
echo "     GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/google-tts.json"
echo
echo "   자격증명을 다시 로그인해서 바꿨다면 이 스크립트를 다시 실행할 것."
