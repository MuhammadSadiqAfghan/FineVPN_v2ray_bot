#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "فایل .env ساخته شد. آن را تکمیل کن و دوباره ./start.sh را اجرا کن."
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker روی این سرور نصب نیست. ابتدا Docker Engine و افزونه Docker Compose را نصب کن." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "افزونه Docker Compose در دسترس نیست." >&2
  exit 1
fi
for key in BOT_TOKEN DOMAIN VPN_PANEL_DOMAIN SECRET_KEY VPN_PANEL_ADMIN_USERNAME VPN_PANEL_ADMIN_PASSWORD; do
  value="$(grep -m1 -E "^${key}=" .env | cut -d= -f2- || true)"
  if [[ -z "$value" || "$value" == *example.com* || "$value" == *CHANGE_* || "$value" == *PASTE_* || "$value" == REPLACE_* ]]; then
    echo "مقدار $key در فایل .env باید تکمیل شود." >&2
    exit 1
  fi
done
if grep -q '^WEBAPP_URL=https://vpn.example.com' .env; then
  echo "WEBAPP_URL هنوز نمونه است؛ دامنه واقعی مینی‌اپ را قرار بده." >&2
  exit 1
fi
if grep -q '^PANEL_PUBLIC_URL=https://panel.example.com' .env; then
  echo "PANEL_PUBLIC_URL هنوز نمونه است؛ دامنه واقعی پنل را قرار بده." >&2
  exit 1
fi
docker compose config -q
docker compose up -d --build
echo
echo "FineVPN شروع شد. وضعیت سرویس‌ها:"
docker compose ps
echo
echo "گزارش‌ها: ./logs.sh"
