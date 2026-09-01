# ZhiTan

[![CI](https://github.com/HBelike/ZhiTan/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/HBelike/ZhiTan/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

ZhiTan is an open-source job-search workspace for candidates. It turns resumes, job descriptions, public interview material, and ongoing conversations into a persistent workflow for job discovery, fit analysis, outreach, and interview preparation.

The core localhost stack starts without model, email, Firecrawl, LangSmith, Docling, or media-provider credentials. Provider-backed actions stay unavailable until configured; account bootstrap, password login, navigation, and system configuration remain usable.

## Core capabilities

- persistent candidate profiles, resumes, conversations, and job records;
- job discovery and structured job-library workflows;
- resume-to-role fit analysis and evidence-backed recommendations;
- greeting generation and browser-assistant integration;
- interview-material collection and live interview support;
- encrypted per-user model credentials and configurable provider profiles.

The resume assistant, evaluation center, and workbench implementations remain in the repository for future development, but their public routes are not mounted.

## Five-minute localhost Quickstart

Requirements: Git, Docker Desktop or Docker Engine with Compose v2, and Python 3.12 or 3.13 for the cross-platform setup helper.

Windows PowerShell:

```powershell
git clone https://github.com/HBelike/ZhiTan.git
Set-Location ZhiTan
.\scripts\setup_quickstart.ps1 -NonInteractive
docker compose --env-file .env.quickstart up -d --build --wait
docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py
Invoke-RestMethod http://127.0.0.1:18081/api/ready
```

Linux or macOS:

```bash
git clone https://github.com/HBelike/ZhiTan.git
cd ZhiTan
./scripts/setup_quickstart.sh
docker compose --env-file .env.quickstart up -d --build --wait
docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py
curl --fail http://127.0.0.1:18081/api/ready
```

Open <http://127.0.0.1:18081>, then sign in with the administrator email shown by the bootstrap screen and the password entered in the terminal.

Only Web is published to the host:

```dotenv
ZHITAN_HTTP_PORT=18081
```

PostgreSQL, API, migration, and Worker remain inside the Compose network. Compose derives resource names from the clone directory or `COMPOSE_PROJECT_NAME`, so separate clones do not share containers or data volumes.

Stop the instance while retaining data:

```bash
docker compose --env-file .env.quickstart down
```

Permanently delete this instance's Compose volumes:

```bash
docker compose --env-file .env.quickstart down -v
```

`down -v` is destructive. It removes the current Compose project's PostgreSQL, application-data, Skill, and credential-key volumes.

## Architecture

```text
Browser -> Nginx Web -> FastAPI -> PostgreSQL/pgvector
                             |
                             +-> persistent Agent Worker

PostgreSQL healthy -> Alembic migration completed -> API/Worker ready -> Web ready
```

- `/api/health` is process liveness.
- `/api/ready` checks PostgreSQL, Alembic head, and writable application storage.
- The default stack excludes Caddy, Scheduler, Gotenberg, Docling, and Skill seeds.
- Production and document services are added through Compose overlays.

## Documentation

- [Getting started](docs/getting-started.md)
- [Local development](docs/local-development.md)
- [Production deployment](docs/production-deployment.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Safety boundaries](SAFETY.md)

## Project status

ZhiTan is a developer preview. The `master` branch is the current supported source line and is gated by Python, Web, browser-extension, build, Compose, migration, readiness, Worker, and real-login checks.

## License

ZhiTan is licensed under [Apache License 2.0](LICENSE). Third-party packages, container images, and optional integrations retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
