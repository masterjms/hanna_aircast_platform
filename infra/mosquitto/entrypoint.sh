#!/bin/sh
# mosquitto 기동 + 단말별 계정(passwd)·마을별 권한(aclfile) 감시 루프.
#
# 백엔드는 mosquitto 컨테이너에 신호를 보낼 수 없다(도커 소켓을 안 물린다).
# 대신 공유 볼륨(/mosquitto/dynamic)에 passwd.generated / aclfile.generated 를
# 떨어뜨리고, 이 스크립트의 감시 루프가:
#   1) 소유·권한을 mosquitto 사용자 0600 으로 맞춰 /mosquitto/data/ 로 설치
#   2) SIGHUP 으로 리로드 (mosquitto 는 exec 되어 PID 1)
#   3) 지금 적용된 aclfile 의 md5 를 aclfile.applied 에 적는다
#
# 3) 은 백엔드가 "ACL 먼저, CONFIG 는 그다음"을 지키려고 읽는다. 단말을 다른 마을로 옮길 때
# 권한이 설치되기 전에 CONFIG 가 가면, 단말은 새 topic 을 구독해 두고도 설치 전까지 나간
# 방송을 못 받는다(mosquitto 는 전달 시점에 ACL 을 본다 — 2026-09-15 실측).
#
# 변경 감지는 내용(md5)으로 한다. 예전의 stat %Y 는 초 단위라, 같은 초 안에 두 번 쓰면
# 두 번째 내용이 다음 변경 때까지 설치되지 않았다.
#
# 백엔드(uid 10001)가 볼륨에 쓸 수 있도록 기동 때 소유자를 넘겨준다.
set -eu

DYN=/mosquitto/dynamic
DATA=/mosquitto/data
SEED=/mosquitto/config

digest() {  # 파일 내용 md5, 없으면 none
    if [ -f "$1" ]; then md5sum "$1" | cut -d' ' -f1; else echo none; fi
}

# 설치: 같은 파일시스템 안에서 임시 파일 → 원자적 교체. 리로드 순간에 반쯤 쓴
# 파일이 읽히는 일이 없게 한다.
install_file() {  # $1=generated 원본, $2=설치 위치
    cp "$1" "$2.tmp"
    chown mosquitto:mosquitto "$2.tmp"
    chmod 600 "$2.tmp"
    mv "$2.tmp" "$2"
}

# 원본을 먼저 복사해 둔 사본의 md5 로 판정한다 — 복사 도중 백엔드가 파일을 바꿔도
# "설치한 내용"과 "보고하는 md5"가 어긋나지 않는다. 바뀌었으면 설치하고 md5 를 출력.
install_if_changed() {  # $1=generated 원본, $2=설치 위치, $3=지금 설치본 md5
    [ -f "$1" ] || return 1
    cp "$1" "$2.new"
    cur="$(digest "$2.new")"
    if [ "$cur" = "$3" ]; then
        rm -f "$2.new"
        return 1
    fi
    chown mosquitto:mosquitto "$2.new"
    chmod 600 "$2.new"
    mv "$2.new" "$2"
    echo "$cur"
}

report_acl() {  # $1=적용된 aclfile md5
    echo "$1" > "$DYN/aclfile.applied.tmp"
    mv "$DYN/aclfile.applied.tmp" "$DYN/aclfile.applied"
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
    last_pw="$(digest "$DATA/passwd")"
    last_acl="$(digest "$DATA/aclfile")"
    # mosquitto 가 기동하며 이 aclfile 을 읽는다. 기동 직후부터 적용 보고가 있어야 백엔드가
    # 기다릴 기준을 안다.
    report_acl "$last_acl"
    while :; do
        sleep 1
        changed=0
        if cur="$(install_if_changed "$DYN/passwd.generated" "$DATA/passwd" "$last_pw")"; then
            last_pw="$cur"; changed=1
        fi
        acl_changed=0
        if cur="$(install_if_changed "$DYN/aclfile.generated" "$DATA/aclfile" "$last_acl")"; then
            last_acl="$cur"; changed=1; acl_changed=1
        fi
        if [ "$changed" = 1 ]; then
            kill -HUP 1
            [ "$acl_changed" = 1 ] && report_acl "$last_acl"
            echo "passwd/aclfile 갱신 설치 + 리로드 완료"
        fi
    done
) &

# 원본 이미지 entrypoint 를 거쳐 mosquitto 를 PID 1 로 exec 한다.
exec /docker-entrypoint.sh mosquitto -c /mosquitto/config/mosquitto.conf
