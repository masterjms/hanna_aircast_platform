#!/bin/sh
# mosquitto 기동 + 단말별 계정(passwd)·마을별 권한(aclfile) 감시 루프.
#
# 백엔드는 mosquitto 컨테이너에 신호를 보낼 수 없다(도커 소켓을 안 물린다).
# 대신 공유 볼륨(/mosquitto/dynamic)에 passwd.generated / aclfile.generated 를
# 떨어뜨리고, 이 스크립트의 감시 루프가:
#   1) 소유·권한을 mosquitto 사용자 0600 으로 맞춰 /mosquitto/data/ 로 설치
#   2) SIGHUP 으로 리로드 (mosquitto 는 exec 되어 PID 1)
#
# 백엔드(uid 10001)가 볼륨에 쓸 수 있도록 기동 때 소유자를 넘겨준다.
set -eu

DYN=/mosquitto/dynamic
DATA=/mosquitto/data
SEED=/mosquitto/config

# 설치: 같은 파일시스템 안에서 임시 파일 → 원자적 교체. 리로드 순간에 반쯤 쓴
# 파일이 읽히는 일이 없게 한다.
install_file() {  # $1=generated 원본, $2=설치 위치
    cp "$1" "$2.tmp"
    chown mosquitto:mosquitto "$2.tmp"
    chmod 600 "$2.tmp"
    mv "$2.tmp" "$2"
}

# 부트스트랩: generated 가 있으면 그것, 없고 설치본도 없으면 시드(리포 파일)를 쓴다.
bootstrap() {  # $1=이름(passwd|aclfile)
    if [ -f "$DYN/$1.generated" ]; then
        install_file "$DYN/$1.generated" "$DATA/$1"
    elif [ ! -f "$DATA/$1" ]; then
        if [ -f "$SEED/$1" ]; then
            cp "$SEED/$1" "$DATA/$1"
        else
            : > "$DATA/$1"
        fi
        chown mosquitto:mosquitto "$DATA/$1"
        chmod 600 "$DATA/$1"
    fi
}

mkdir -p "$DYN"
chown 10001 "$DYN" || true   # 백엔드 컨테이너의 xwifi 사용자

bootstrap passwd
bootstrap aclfile

(
    last_pw="$(stat -c %Y "$DYN/passwd.generated" 2>/dev/null || echo none)"
    last_acl="$(stat -c %Y "$DYN/aclfile.generated" 2>/dev/null || echo none)"
    while :; do
        sleep 2
        changed=0
        cur="$(stat -c %Y "$DYN/passwd.generated" 2>/dev/null || echo none)"
        if [ "$cur" != "$last_pw" ] && [ "$cur" != none ]; then
            last_pw="$cur"; install_file "$DYN/passwd.generated" "$DATA/passwd" && changed=1
        fi
        cur="$(stat -c %Y "$DYN/aclfile.generated" 2>/dev/null || echo none)"
        if [ "$cur" != "$last_acl" ] && [ "$cur" != none ]; then
            last_acl="$cur"; install_file "$DYN/aclfile.generated" "$DATA/aclfile" && changed=1
        fi
        if [ "$changed" = 1 ]; then
            kill -HUP 1
            echo "passwd/aclfile 갱신 설치 + 리로드 완료"
        fi
    done
) &

# 원본 이미지 entrypoint 를 거쳐 mosquitto 를 PID 1 로 exec 한다.
exec /docker-entrypoint.sh mosquitto -c /mosquitto/config/mosquitto.conf
