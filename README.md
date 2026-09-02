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

## Product walkthrough

ZhiTan is designed as one connected workflow: understand the candidate, compare that evidence with real roles, turn the match into outreach, and stay useful when the interview begins.

### 1. Work with a persistent career assistant

The assistant keeps conversations, selected resumes, job context, and model configuration together. A live role-fit panel keeps the current target visible while the candidate asks questions or plans the next step.

[![ZhiTan career assistant with persistent conversations and a role-fit panel](docs/assets/product/career-assistant.png)](docs/assets/product/career-assistant.png)

### 2. Inspect resume-to-role fit by evidence

Instead of returning one opaque score, ZhiTan breaks fit analysis into dimensions such as education, experience, skills, seniority, and keyword coverage. Each detail view shows which resume evidence supports a requirement and which gaps still need attention.

[![Resume fit analysis showing the work-experience score and supporting evidence](docs/assets/product/resume-fit-analysis.png)](docs/assets/product/resume-fit-analysis.png)

### 3. Discover real job postings

Job discovery searches supported public sources and returns the original role details for review. Candidates can compare salary, location, experience, employer, responsibilities, and requirements before adding a role to their workflow.

[![Live job discovery results with real posting details](docs/assets/product/live-job-discovery.png)](docs/assets/product/live-job-discovery.png)

### 4. Review personalized greetings before batch outreach

The batch workflow separates selection from generation and dispatch. Candidates choose several relevant roles, generate a greeting from resume evidence and the selected job description, review every message, and explicitly confirm before sending.

<p align="center">
  <a href="docs/assets/product/batch-greeting-role-selection.png"><img src="docs/assets/product/batch-greeting-role-selection.png" width="49%" alt="Selecting several real roles for batch outreach"></a>
  <a href="docs/assets/product/batch-greeting-review.png"><img src="docs/assets/product/batch-greeting-review.png" width="49%" alt="Reviewing a personalized greeting before confirming batch delivery"></a>
</p>

### 5. Turn a live interview question into an answer draft

With explicit user authorization, the interview copilot transcribes system audio in real time and organizes the complete question. It then uses the configured model to prepare a concise opening and a structured answer. The setup and active-session screens keep audio scope, provider choice, connection state, and session history visible.

<p align="center">
  <a href="docs/assets/product/live-interview-setup.png"><img src="docs/assets/product/live-interview-setup.png" width="49%" alt="Configuring transcription and answer models for a live interview"></a>
  <a href="docs/assets/product/live-interview-assistant.png"><img src="docs/assets/product/live-interview-assistant.png" width="49%" alt="Live interview assistant waiting for a complete question"></a>
</p>

> [!NOTE]
> Screenshots come from a development environment and contain sample candidate and job data. Live job discovery, model-backed generation, email, and streaming speech recognition require their corresponding provider credentials.

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

Quickstart defaults to CLI administrator bootstrap and password login. Production-style email-code login, public registration, and password recovery appear only after `PLATFORM_PUBLIC_REGISTRATION_ENABLED=true` and a complete email Provider configuration; see [Getting started](docs/getting-started.md#enable-production-style-registration-locally). If an existing local administrator password is unavailable, reset it without deleting data:

```bash
docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py --reset-password
```

### Choose a login path

Email delivery is optional. Password login remains available without Resend: create the first administrator with the interactive bootstrap command above, or use `--reset-password` when the local password is unknown. This prevents a missing or unavailable email Provider from locking an operator out.

To enable the registration screen shown in the product screenshots, configure your own Resend account:

1. [Create a Resend account](https://resend.com/signup).
2. [Add and verify a domain](https://resend.com/docs/add-a-domain); a dedicated sending subdomain is recommended.
3. [Create an API key](https://resend.com/docs/create-an-api-key).
4. Add these operator-owned values to the ignored `.env.quickstart` file:

```dotenv
PLATFORM_PUBLIC_REGISTRATION_ENABLED=true
RESEND_API_KEY=re_your_own_key
RESEND_FROM_ADDRESS=ZhiTan <no-reply@notifications.your-domain.example>
```

Keep the setup-generated `PLATFORM_EMAIL_CODE_SECRET`, then recreate `career-api` as described in [Getting started](docs/getting-started.md#enable-production-style-registration-locally). Never use a maintainer's key, publish a live key, or commit `.env.quickstart`; each deployment must use credentials owned by its operator. If delivery later stops, existing users can still use password login and the operator can reset the administrator password from the server terminal.

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
