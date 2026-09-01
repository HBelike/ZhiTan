# Local Development

Source development is separate from the Docker Quickstart. Use a repository-local virtual environment and a clone-specific PostgreSQL Compose project.

## Automated setup

Windows:

```powershell
.\scripts\setup_dev.ps1
```

Linux or macOS:

```bash
./scripts/setup_dev.sh
```

The scripts create `.venv`, install the lock, run `npm ci`, generate `.env.career-assistant` without overwriting existing values, and check the database/API/Vite ports.

## Manual Windows setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
npm --prefix web-ui ci
Copy-Item .env.career-assistant.example .env.career-assistant
docker compose --env-file .env.career-assistant up -d career-postgres
.\.venv\Scripts\python.exe -m alembic upgrade head
```

## Manual Linux or macOS setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
npm --prefix web-ui ci
cp .env.career-assistant.example .env.career-assistant
docker compose --env-file .env.career-assistant up -d career-postgres
.venv/bin/python -m alembic upgrade head
```

Replace the example database password before starting PostgreSQL. The setup scripts do this automatically with a URL-safe random value.

## Development ports

```dotenv
CAREER_POSTGRES_PORT=54329
PREVIEW_SERVER_PORT=18080
VITE_PORT=5173
VITE_API_PROXY_TARGET=http://127.0.0.1:18080
```

When changing the PostgreSQL port, update `CAREER_DATABASE_URL`. When changing the API port, update `VITE_API_PROXY_TARGET`. The setup scripts stop with a named conflict instead of attaching to an existing service.

## Run the processes

Use separate terminals after PostgreSQL is healthy and migration completes.

Windows:

```powershell
.\.venv\Scripts\python.exe preview_server.py --host 127.0.0.1 --port 18080
.\.venv\Scripts\python.exe scripts/run_career_agent_worker.py
npm --prefix web-ui run dev -- --host 127.0.0.1 --port 5173
```

Linux or macOS:

```bash
.venv/bin/python preview_server.py --host 127.0.0.1 --port 18080
.venv/bin/python scripts/run_career_agent_worker.py
npm --prefix web-ui run dev -- --host 127.0.0.1 --port 5173
```

Migrations are explicit in source development. Do not start API or Worker against a database that has not reached Alembic head.

## Tests and builds

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix web-ui test
npm --prefix web-ui run build
npm --prefix browser-extension/job-library test
```

```bash
.venv/bin/python -m pytest -q
npm --prefix web-ui test
npm --prefix web-ui run build
npm --prefix browser-extension/job-library test
```

Tests must use tracked fixtures and may not depend on user-level Skill/plugin caches or files from another clone.
