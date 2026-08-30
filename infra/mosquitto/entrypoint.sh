#!/bin/sh
# mosquitto 기동 + 단말별 계정 passwd 감시 루프.
#
# 백엔드는 mosquitto 컨테이너에 신호를 보낼 수 없다(도커 소켓을 안 물린다).
# 대신 공유 볼륨(/mosquitto/dynamic)에 passwd.generated 를 떨어뜨리고,
# 이 스크립트의 감시 루프가:
#   1) 소유·권한을 mosquitto 사용자 0600 으로 맞춰 /mosquitto/data/passwd 로 설치
#   2) SIGHUP 으로 리로드 (mosquitto 는 exec 되어 PID 1)
#
# 백엔드(uid 10001)가 볼륨에 쓸 수 있도록 기동 때 소유자를 넘겨준다.
set -eu

GEN=/mosquitto/dynamic/passwd.generated
LIVE=/mosquitto/data/passwd

install_passwd() {
    # 같은 파일시스템 안에서 임시 파일 → 원자적 교체. 리로드 순간에 반쯤 쓴
    # 파일이 읽히는 일이 없게 한다.
    cp "$GEN" "$LIVE.tmp"
    chown mosquitto:mosquitto "$LIVE.tmp"
    chmod 600 "$LIVE.tmp"
    mv "$LIVE.tmp" "$LIVE"
}

mkdir -p /mosquitto/dynamic
chown 10001 /mosquitto/dynamic || true   # 백엔드 컨테이너의 xwifi 사용자

if [ -f "$GEN" ]; then
    install_passwd
elif [ ! -f "$LIVE" ]; then
    # 첫 전환 부트스트랩: 백엔드가 아직 안 내보냈으면 기존 passwd 를 물려받는다.
    # 그것도 없으면 빈 파일 — 아무도 못 붙지만 기동은 되고, 백엔드가 뜨면 채워진다.
    if [ -f /mosquitto/config/passwd ]; then
        cp /mosquitto/config/passwd "$LIVE"
    else
        : > "$LIVE"
    fi
    chown mosquitto:mosquitto "$LIVE"
    chmod 600 "$LIVE"
fi

(
    last="$(stat -c %Y "$GEN" 2>/dev/null || echo none)"
    while :; do
        sleep 2
        cur="$(stat -c %Y "$GEN" 2>/dev/null || echo none)"
        if [ "$cur" != "$last" ] && [ "$cur" != none ]; then
            last="$cur"
            if install_passwd; then
                kill -HUP 1
                echo "passwd 갱신 설치 + 리로드 완료"
            fi
        fi
    done
) &

# 원본 이미지 entrypoint 를 거쳐 mosquitto 를 PID 1 로 exec 한다.
exec /docker-entrypoint.sh mosquitto -c /mosquitto/config/mosquitto.conf
