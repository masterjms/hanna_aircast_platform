#!/usr/bin/env bash
#
# nginx 접근 로그 하루치 정리 (문제점 70번 10/15 ⑥, 2026-10-17) — 매일 cron 으로.
#
#   bash scripts/rotate-nginx-logs.sh
#
# 접근 로그는 nginx-logs 볼륨(/var/log/nginx-archive/access.log)에 쌓인다(docker logs 쪽은 크기 상한이
# 있어 며칠이면 밀려난다). 이 스크립트가 하루치를 access-YYYYMMDD.log.gz 로 떼어 내고, 보관 기간
# (NGINX_LOG_KEEP_DAYS, 기본 365일)이 지난 파일을 지운다. 공공 기준 「접속기록 1년 이상」을 맞추는 쪽은
# 화면 로그인 기록(login_events, 2년)이고, 이 파일은 웹 접근 전체(단말 다운로드 포함)의 보조 기록이다.
#
# cron 예(매일 00:10 KST — 서버 시간대가 KST 일 때):
#   ( crontab -l 2>/dev/null; echo '10 0 * * * cd ~/xwifi-server && bash scripts/rotate-nginx-logs.sh >> ~/rotate-nginx.log 2>&1' ) | crontab -
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

KEEP_DAYS="${NGINX_LOG_KEEP_DAYS:-365}"
STAMP="$(date +%Y%m%d -d yesterday 2>/dev/null || date +%Y%m%d)"

docker compose exec -T nginx sh -c "
    set -e
    cd /var/log/nginx-archive
    if [ -s access.log ]; then
        mv access.log access-$STAMP.log
        nginx -s reopen
        sleep 1
        gzip -f access-$STAMP.log
    fi
    find . -name 'access-*.log.gz' -mtime +$KEEP_DAYS -delete
    echo \"\$(date '+%F %T') 정리 완료 — 보관 파일 \$(ls access-*.log.gz 2>/dev/null | wc -l)개, \$(du -sh . | cut -f1)\"
"
