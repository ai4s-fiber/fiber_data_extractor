#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

mkdir -p /var/www/certbot

docker run --rm \
    -v /etc/letsencrypt:/etc/letsencrypt \
    -v /var/www/certbot:/var/www/certbot \
    certbot/certbot renew --webroot -w /var/www/certbot --quiet

docker compose exec -T frontend nginx -s reload
echo "$(date): certificate renewed"
