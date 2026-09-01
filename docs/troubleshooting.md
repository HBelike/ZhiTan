# Troubleshooting

## Port 18081 is already in use

The setup helper reports `ZHITAN_HTTP_PORT=18081`. Either stop the process that owns it or choose another unused port in `.env.quickstart`, then open the matching localhost URL.

Quickstart publishes no PostgreSQL or API port. If those ports appear on the host, inspect the Compose project labels; they belong to another stack or a source-development instance.

## A second clone sees unexpected containers or data

Run:

```bash
docker compose --env-file .env.quickstart config --format json
docker compose --env-file .env.quickstart ps
```

There must be no fixed top-level project name, `container_name`, or global volume `name`. Set a unique `COMPOSE_PROJECT_NAME` when automation runs multiple instances from similarly named directories.

## Migration never completes

```bash
docker compose --env-file .env.quickstart logs career-postgres career-migrate
docker compose --env-file .env.quickstart ps -a
```

PostgreSQL must become healthy before `career-migrate` runs. API and Worker intentionally wait for migration exit code 0. Fix the migration error; do not bypass the dependency.

## `/api/health` works but `/api/ready` fails

`/api/health` only proves the API process is alive. `/api/ready` checks database connectivity, Alembic head, and writable application storage. Read its `checks` array and inspect only the named service logs. Readiness details are intentionally stripped of connection URLs and Secrets.

## Worker is running but unhealthy

```bash
docker compose --env-file .env.quickstart logs career-agent-worker
docker compose --env-file .env.quickstart exec career-agent-worker python scripts/check_career_worker_health.py
```

The probe executes `SELECT 1`. Check that migration completed and that the Worker uses the same `CAREER_DATABASE_URL` as API.

## Login page asks for administrator initialization

```bash
docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py --check
docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py
```

The second command must be interactive. Do not use `-T`, a password argument, or a pasted command containing the password.

## Registration, email login, or password reset is missing

This is expected when Resend, a verified from-address, and `PLATFORM_EMAIL_CODE_SECRET` are not all configured. Password login and CLI administrator bootstrap remain available. The UI exposes email flows only after the backend reports a complete delivery configuration.

## A Provider action says “not configured”

The core stack deliberately starts without external keys. Configure the relevant model, Firecrawl, email, observability, document, or media Provider through the documented server-side setting. Missing Provider credentials must not be placed in frontend code or committed environment files.

## Stop without deleting data

```bash
docker compose --env-file .env.quickstart down
```

## Permanently reset the local instance

```bash
docker compose --env-file .env.quickstart down -v
```

The reset command deletes this Compose project's PostgreSQL, application-data, Skill, and credential-key volumes. It cannot be undone.
