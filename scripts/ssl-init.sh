#!/bin/bash
# 首次申请 Let's Encrypt 证书（域名已解析、安全组放行 80/443）
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

DOMAIN="${SSL_DOMAIN:?Set SSL_DOMAIN before requesting certificates}"
EMAIL="${SSL_EMAIL:-admin@${DOMAIN}}"

echo "==> 安装 certbot..."
if ! command -v certbot &>/dev/null; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot
fi

mkdir -p /var/www/certbot

echo "==> 临时停止前端以释放 80 端口..."
docker compose stop frontend

echo "==> 申请证书..."
certbot certonly --standalone \
    -d "$DOMAIN" -d "www.$DOMAIN" \
    --email "$EMAIL" --agree-tos --no-eff-email --non-interactive

echo "==> 构建并启动 HTTPS 前端..."
docker compose build frontend
docker compose up -d

echo "==> 配置自动续期..."
chmod +x scripts/renew-cert.sh
CRON_LINE="0 3 * * * ${PROJECT_DIR}/scripts/renew-cert.sh >> /var/log/certbot-renew.log 2>&1"
(crontab -l 2>/dev/null | grep -v 'renew-cert.sh' || true; echo "$CRON_LINE") | crontab -

echo "==> 完成: https://${DOMAIN}"
