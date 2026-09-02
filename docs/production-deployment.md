# Production Deployment

Production deployment is an operator-owned process. These files define a baseline topology; they do not configure DNS, provision a server, rotate Secrets, create backups, or authorize deployment to any existing environment.

## Topology

Merge the core and production overlays:

```bash
docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml config --quiet
docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml up -d --build --wait
```

The production overlay adds Caddy and the optional content Scheduler. Add document conversion only when its resource and privacy costs are understood:

```bash
docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml -f compose.document.yaml config --quiet
```

## Required operator decisions

1. Copy `.env.production.example` to ignored `.env.production`.
2. Set `ZHITAN_ENV_FILE=.env.production` and the real `APP_DOMAIN`.
3. Generate independent database, email-code, and credential-encryption Secrets.
4. Keep `PLATFORM_AUTH_REQUIRED=true` and `PLATFORM_CLI_BOOTSTRAP_ONLY=true` on public instances.
5. Set `RESEND_API_KEY` and `RESEND_FROM_ADDRESS` to a verified sender before enabling `PLATFORM_PUBLIC_REGISTRATION_ENABLED=true`.
6. Keep `CAREER_ALLOW_PAID_PROFILES=true` and `CAREER_ENABLE_LOCAL_OLLAMA_PROFILE=true` when configured model profiles should be usable. Set either to `false` only as an intentional organization policy; enabling them never calls a model during startup.
7. Configure DNS and verify Caddy TLS before sharing the URL.
8. Define database backup, restore, host patching, monitoring, and Secret-rotation procedures.

PostgreSQL, API, Worker, Gotenberg, and Docling must not be published directly to the internet. Caddy is the only public ingress.

## Bootstrap and readiness

```bash
docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml exec career-api python scripts/bootstrap_first_admin.py
curl --fail https://your-domain.example/api/health
curl --fail https://your-domain.example/api/ready
```

Do not add a password command-line argument or pipe production passwords through automation. Public registration becomes usable only with complete email delivery configuration; set `PLATFORM_PUBLIC_REGISTRATION_ENABLED=false` when an operator intentionally wants a closed-registration deployment.

If the administrator password is lost, reset it without deleting PostgreSQL or application volumes:

```bash
docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml exec career-api python scripts/bootstrap_first_admin.py --reset-password
```

After enabling registration, inspect `/api/auth/bootstrap-status` and require both `public_registration_enabled` and `email_auth_enabled` to be `true` before sharing the registration URL.

## Provider validation

The mandatory clean-room CI does not call paid or authenticated Providers. After deployment, validate only the Providers configured for that instance: model access, email delivery, Firecrawl, LangSmith, document conversion, media generation, and object storage. Use sanitized test data, not a real candidate resume.

## Updates

Deploy immutable releases or reviewed `master` commits. Before updating, review migrations and release notes, validate the merged Compose configuration, back up operator-owned data, then rebuild. Never reuse another clone's environment file or named volumes.
