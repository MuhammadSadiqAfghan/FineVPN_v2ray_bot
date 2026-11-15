#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups
docker compose exec -T api sh -c 'tar czf - -C /data .' > "backups/finevpn-data-${STAMP}.tar.gz"
docker compose exec -T vpn-panel sh -c 'tar czf - -C /data .' > "backups/vpn-panel-data-${STAMP}.tar.gz"
echo "پشتیبان‌ها در پوشه backups ذخیره شدند."
