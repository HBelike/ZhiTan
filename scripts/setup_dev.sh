#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
VENV_PYTHON=.venv/bin/python
"$VENV_PYTHON" scripts/setup_common.py dev

if [ -f requirements.lock.txt ]; then
  "$VENV_PYTHON" -m pip install -r requirements.lock.txt
else
  "$VENV_PYTHON" -m pip install -r requirements.txt -r requirements-career-assistant.txt -r requirements-development.txt
fi
npm --prefix web-ui ci

echo "开发环境准备完成。请先启动 PostgreSQL，等待 healthy 后执行："
echo ".venv/bin/python -m alembic upgrade head"
echo ".venv/bin/python preview_server.py --host 127.0.0.1 --port 18080"
echo ".venv/bin/python scripts/run_career_agent_worker.py"
echo "npm --prefix web-ui run dev -- --host 127.0.0.1 --port 5173"
