#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

command -v python3 >/dev/null 2>&1 || { echo "未找到 Python 3.12 或 3.13。" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "未找到 Docker Engine。" >&2; exit 1; }

docker compose version >/dev/null
python3 scripts/setup_common.py quickstart

echo "下一步：docker compose --env-file .env.quickstart up -d --build --wait"
