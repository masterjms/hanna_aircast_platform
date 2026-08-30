#!/usr/bin/env bash
#
# MQTT 계정 생성 (운영 서버에서 1회 실행)
#
#   bash scripts/setup-mqtt-users.sh
#
# ACL(infra/mosquitto/config/aclfile)은 저장소에 있지만 비밀번호 파일은 없다 —
# 비밀값이라 커밋하지 않는다. 이 스크립트가 서버에서 만든다.
#
# 계정 두 개:
#   xwifi-server  백엔드용. iotradio/# 전체 읽기·쓰기
#   xwifi-device  단말용 공유 계정 — 이행기 한시 (아래 참고)
#
# 2026-08-30 부터 단말은 단말별 계정(username=MAC)이 기본이다. 단말별 계정은
# 이 스크립트가 아니라 **백엔드가 발행·관리**한다(등록 API → DB → passwd 재생성
# → mosquitto entrypoint 감시 루프가 설치+리로드). 이 스크립트는 최초 구축 때
# 서버 계정과 이행기 공유 계정만 만든다. 여기서 만든 passwd 는 첫 기동 때
# /mosquitto/data/passwd 로 복사돼 시드가 되고, 백엔드가 뜨면 재생성본으로
# 대체된다 — 그때 공유 계정이 유지되려면 .env 에 MQTT_DEVICE_PASSWORD 가
# 있어야 한다(없으면 공유 계정이 빠진 채 재생성된다).
#
# ⚠ 공유 계정은 유효 계정 하나만 알면 client_id 를 바꿔 다른 단말을 사칭할 수
#   있는 구멍이 있다. 전 단말이 단말별 계정을 받으면 .env 에서
#   MQTT_DEVICE_PASSWORD 를 지우고 aclfile 의 %c 블록을 삭제한다.
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF_DIR="$REPO_DIR/infra/mosquitto/config"
PASSWD="$CONF_DIR/passwd"

if [ -f "$PASSWD" ]; then
    echo "!! $PASSWD 가 이미 있다."
    echo "   덮어쓰면 기존 비밀번호가 사라지고 백엔드·단말이 접속하지 못한다."
    read -rp "   그래도 새로 만들까? (yes 입력) " ans
    [ "$ans" = "yes" ] || { echo "취소했다."; exit 1; }
    rm -f "$PASSWD"
fi

gen() { openssl rand -base64 24 | tr -d '/+=' | cut -c1-24; }

SERVER_PW="${MQTT_SERVER_PASSWORD:-$(gen)}"
DEVICE_PW="${MQTT_DEVICE_PASSWORD:-$(gen)}"

# 생성과 권한 조정을 **한 컨테이너 안에서** 끝낸다.
#
# mosquitto_passwd 는 파일을 0600 root 소유로 만든다. 그러면 정작 mosquitto
# 데몬(uid 1883)이 못 읽어서 "Unable to open pwfile" 로 기동이 실패한다.
# 호스트에서 chmod 하려 해도 root 소유라 sudo 없이는 안 되고, sudo 로 644 를
# 주면 이번엔 해시가 담긴 파일이 누구나 읽을 수 있게 된다.
# 컨테이너 안에서 root 권한으로 소유자를 mosquitto 로 바꾸고 0600 을 유지한다.
docker run --rm -v "$CONF_DIR:/c" --entrypoint sh eclipse-mosquitto:2 -c "
    set -e
    mosquitto_passwd -c -b /c/passwd xwifi-server '$SERVER_PW'
    mosquitto_passwd    -b /c/passwd xwifi-device '$DEVICE_PW'
    chown 1883:1883 /c/passwd
    chmod 600 /c/passwd
    # mosquitto 2.x 가 경고하고, 이후 버전은 로드를 거부한다.
    chown 1883:1883 /c/aclfile
    chmod 640 /c/aclfile
"

cat <<EOF

생성 완료: $PASSWD

  .env 에 넣을 값
    MQTT_USERNAME=xwifi-server
    MQTT_PASSWORD=$SERVER_PW
    MQTT_DEVICE_PASSWORD=$DEVICE_PW   # 이행기 공유 계정 유지용. 전환 완료 후 삭제

  ESP32 팀에 전달할 단말 공유 계정 (이행기 한시 — 단말별 계정이 기본)
    username: xwifi-device
    password: $DEVICE_PW

이 출력은 지금 한 번만 보인다. 안전한 곳에 즉시 보관할 것.
EOF
