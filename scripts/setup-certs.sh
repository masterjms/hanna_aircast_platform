#!/usr/bin/env bash
#
# TLS 인증서 최초 발급 + 자동 갱신 설정 (운영 서버에서 1회 실행)
#
#   sudo bash scripts/setup-certs.sh hanna-aircast.co.kr admin@example.com
#
# 하는 일:
#   1. certbot 으로 인증서 발급 (nginx 가 아직 없으면 standalone, 있으면 webroot)
#   2. 발급본을 infra/certs/ 로 복사 — nginx 와 mosquitto 가 여기를 함께 읽는다
#   3. 갱신 훅 등록 — 90일마다 자동 갱신되고, 갱신될 때마다 2번을 다시 한다
#
# 왜 심볼릭 링크가 아니라 복사인가:
#   letsencrypt 의 privkey 는 root 만 읽을 수 있는 0600 이다. mosquitto 컨테이너는
#   비-root 사용자로 돌아서 그대로 마운트하면 읽지 못하고 기동에 실패한다.
#   복사본을 만들고 컨테이너가 읽을 수 있는 권한을 주는 편이 확실하다.
#
set -euo pipefail

DOMAIN="${1:?사용법: setup-certs.sh <도메인> <이메일>}"
EMAIL="${2:?사용법: setup-certs.sh <도메인> <이메일>}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$REPO_DIR/infra/certs"
WEBROOT="$REPO_DIR/infra/certbot-webroot"
LE_DIR="/etc/letsencrypt/live/$DOMAIN"

echo "== 도메인: $DOMAIN"

# ── 0. DNS 가 이 서버를 가리키는지 먼저 본다 ────────────────────────
# 이 확인을 건너뛰고 실패를 반복하면 Let's Encrypt 발급 한도에 걸린다.
MY_IP="$(curl -fsS https://checkip.amazonaws.com || echo '')"
DNS_IP="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || echo '')"
echo "   서버 IP: ${MY_IP:-알 수 없음} / DNS: ${DNS_IP:-응답 없음}"
if [ -n "$MY_IP" ] && [ -n "$DNS_IP" ] && [ "$MY_IP" != "$DNS_IP" ]; then
    echo "!! DNS 가 이 서버를 가리키지 않는다. A 레코드 전파를 기다린 뒤 다시 실행할 것."
    exit 1
fi

command -v certbot >/dev/null || { apt update && apt install -y certbot; }

# ── 1. 발급 ─────────────────────────────────────────────────────────
if [ -d "$LE_DIR" ]; then
    echo "== 이미 발급된 인증서가 있다. 발급을 건너뛴다."
elif docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^xwifi-nginx$'; then
    echo "== nginx 가 떠 있다 → webroot 방식"
    mkdir -p "$WEBROOT"
    certbot certonly --webroot -w "$WEBROOT" \
        -d "$DOMAIN" -d "www.$DOMAIN" \
        --agree-tos -m "$EMAIL" --non-interactive
else
    echo "== nginx 가 없다 → standalone 방식 (80 번 포트를 잠시 쓴다)"
    certbot certonly --standalone \
        -d "$DOMAIN" -d "www.$DOMAIN" \
        --agree-tos -m "$EMAIL" --non-interactive
fi

# ── 2. 컨테이너가 읽는 위치로 복사 ──────────────────────────────────
install_certs() {
    mkdir -p "$CERT_DIR"
    # fullchain 을 쓴다. 리프만 물리면 단말이 신뢰 경로를 완성하지 못한다.
    cp -L "$LE_DIR/fullchain.pem" "$CERT_DIR/server.crt"
    cp -L "$LE_DIR/privkey.pem"   "$CERT_DIR/server.key"
    chmod 644 "$CERT_DIR/server.crt"     # 공개 인증서
    # 개인키는 두 컨테이너가 읽어야 한다:
    #   nginx      마스터 프로세스가 root 라 소유자 권한으로 읽는다
    #   mosquitto  uid 1883 으로 도므로 그룹 권한이 필요하다
    # 644 로 열지 않고 그룹만 허용한다.
    chown root:1883 "$CERT_DIR/server.key"
    chmod 640 "$CERT_DIR/server.key"
    echo "== 인증서를 $CERT_DIR 로 복사했다"
}
install_certs

# ── 3. 갱신 훅 ──────────────────────────────────────────────────────
# certbot 은 갱신에 성공하면 deploy-hook 을 실행한다. 여기서 복사본을 갱신하고
# 두 서비스를 다시 읽게 한다. 이 훅이 없으면 90일 뒤 만료된 인증서를 계속 쓴다.
HOOK=/etc/letsencrypt/renewal-hooks/deploy/xwifi-reload.sh
mkdir -p "$(dirname "$HOOK")"
cat > "$HOOK" <<HOOKEOF
#!/usr/bin/env bash
set -e
cp -L "$LE_DIR/fullchain.pem" "$CERT_DIR/server.crt"
cp -L "$LE_DIR/privkey.pem"   "$CERT_DIR/server.key"
chmod 644 "$CERT_DIR/server.crt"
chown root:1883 "$CERT_DIR/server.key"
chmod 640 "$CERT_DIR/server.key"
cd "$REPO_DIR"
docker compose restart nginx mosquitto
HOOKEOF
chmod +x "$HOOK"
echo "== 갱신 훅 등록: $HOOK"

echo
echo "완료. 갱신 동작을 미리 확인하려면:"
echo "   sudo certbot renew --dry-run"
