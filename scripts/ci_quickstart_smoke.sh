#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

BASE_URL="http://127.0.0.1:${ZHITAN_HTTP_PORT:-18081}"
python3 scripts/verify_quickstart.py \
  --phase pre \
  --base-url "$BASE_URL" \
  --compose-project "${COMPOSE_PROJECT_NAME:-}"

TEST_ADMIN_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
ADMIN_EMAIL=$(sed -n 's/^PLATFORM_ADMIN_EMAIL=//p' .env.quickstart | head -n 1)

printf '%s\n' "$TEST_ADMIN_PASSWORD" | \
  docker compose --env-file .env.quickstart exec -T \
    -e ZHITAN_EPHEMERAL_TEST_MODE=true \
    career-api python scripts/create_ephemeral_test_admin.py

printf '%s\n' "$TEST_ADMIN_PASSWORD" | \
  python3 scripts/verify_quickstart.py \
    --phase post \
    --base-url "$BASE_URL" \
    --admin-email "$ADMIN_EMAIL"

TEST_ADMIN_PASSWORD=
unset TEST_ADMIN_PASSWORD
