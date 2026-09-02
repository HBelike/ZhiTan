# Getting Started

This guide starts an isolated localhost ZhiTan instance with Web, API, Agent Worker, migration, and PostgreSQL/pgvector. No external Provider credentials are required.

## Requirements

- Git;
- Docker Desktop or Docker Engine with Compose v2.20.3 or newer;
- Python 3.12 or 3.13 for the setup helper;
- at least 4 GB of free memory for the core stack.

## Windows PowerShell

```powershell
git clone https://github.com/HBelike/ZhiTan.git
Set-Location ZhiTan
.\scripts\setup_quickstart.ps1 -NonInteractive
docker compose --env-file .env.quickstart up -d --build --wait
```

## Linux or macOS

```bash
git clone https://github.com/HBelike/ZhiTan.git
cd ZhiTan
./scripts/setup_quickstart.sh
docker compose --env-file .env.quickstart up -d --build --wait
```

The helper creates `.env.quickstart` with URL-safe random secrets and refuses to overwrite it. The only published host port is:

```dotenv
ZHITAN_HTTP_PORT=18081
```

To prepare the file manually, copy `.env.quickstart.example` to `.env.quickstart`, replace both `replace-with-setup-generated-value` entries with independent URL-safe random values, and keep the file outside Git.

## Verify readiness

```powershell
Invoke-RestMethod http://127.0.0.1:18081/api/health
Invoke-RestMethod http://127.0.0.1:18081/api/ready
docker compose --env-file .env.quickstart ps
```

```bash
curl --fail http://127.0.0.1:18081/api/health
curl --fail http://127.0.0.1:18081/api/ready
docker compose --env-file .env.quickstart ps
```

`/api/ready` must report `ready: true`. The migration service should be exited with code 0; API, Worker, Web, and PostgreSQL should be healthy.

## Create the first administrator

Run the interactive command from the repository root:

```bash
docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py
```

The script reads the display name and password from the terminal. It does not accept a password argument. Open <http://127.0.0.1:18081> and sign in using the administrator email shown on the page.

Public registration, email-code login, and password reset are hidden until a complete email Provider configuration exists. Password login works without any external Provider.

If the administrator already exists but its password is unavailable, retain the data volumes and reset only that password from an interactive terminal:

```bash
docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py --reset-password
```

The command requires two matching password entries plus an explicit `RESET` confirmation. It revokes existing administrator sessions and never accepts the password as a command-line argument.

## Enable production-style registration locally

The Quickstart enables the public-registration policy by default. Email delivery is still optional: registration UI is exposed only when the complete email Provider configuration below exists, while the CLI-created administrator and password login do not require an email Provider.

To reproduce the production login screen, first prepare an operator-owned Resend account:

1. [Create a Resend account](https://resend.com/signup).
2. [Add and verify a sending domain](https://resend.com/docs/add-a-domain). Using a dedicated subdomain such as `notifications.example.com` keeps sending configuration isolated.
3. [Create an API key](https://resend.com/docs/create-an-api-key).
4. Add the following values to the ignored `.env.quickstart` file:

```dotenv
PLATFORM_PUBLIC_REGISTRATION_ENABLED=true
RESEND_API_KEY=re_your_own_key
RESEND_FROM_ADDRESS=ZhiTan <no-reply@notifications.your-domain.example>
```

Never use a maintainer's key or commit an active credential. Each deployment must use a key controlled by its operator. Keep the generated `PLATFORM_EMAIL_CODE_SECRET`; the from-address must use the verified domain. Recreate API after changing these values:

```bash
docker compose --env-file .env.quickstart up -d --force-recreate career-api
```

Confirm that the backend reports both capabilities as enabled:

```bash
curl --fail http://127.0.0.1:18081/api/auth/bootstrap-status
```

The response must contain `"public_registration_enabled":true` and `"email_auth_enabled":true`. User accounts remain local to this PostgreSQL volume; production accounts are never copied from Git.

If Resend is temporarily unavailable, existing users are not locked out. Password login remains available, and the administrator password can be reset with the interactive `bootstrap_first_admin.py --reset-password` command documented above.

## Model policy defaults

Quickstart enables operator-configured paid and local model profiles:

```dotenv
CAREER_ALLOW_PAID_PROFILES=true
CAREER_ENABLE_LOCAL_OLLAMA_PROFILE=true
```

These switches remove policy-only blocking; they do not call a model automatically and do not make a profile ready without its required credential or endpoint. Set a switch to `false` only when the deployment operator intentionally prohibits that class of model.

## Stop or remove the instance

Stop containers and retain all named volumes:

```bash
docker compose --env-file .env.quickstart down
```

Delete containers and this Compose project's data volumes:

```bash
docker compose --env-file .env.quickstart down -v
```

The second command permanently deletes PostgreSQL data, application data, saved Skills, and the managed credential key for this instance.

## Optional Providers

Provider keys are configured after login or in the ignored environment file, depending on the integration. Missing model, Firecrawl, email, LangSmith, document, and media credentials do not prevent the core stack from becoming ready. Calls that require a missing Provider return a configuration error instead of a fake success.
