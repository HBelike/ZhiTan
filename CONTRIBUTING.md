# Contributing to ZhiTan

Thank you for helping improve ZhiTan. Keep changes focused, evidence-backed, and safe for a public clean checkout.

## Before starting

- Search existing issues and pull requests.
- Discuss large features, schema changes, and new external services before implementation.
- Never commit real resumes, transcripts, cookies, server addresses, API keys, passwords, or production logs.
- Read [SAFETY.md](SAFETY.md) before changing browser automation, interview support, or assessment behavior.

## Development setup

Use the tested setup script:

```powershell
.\scripts\setup_dev.ps1
```

```bash
./scripts/setup_dev.sh
```

Both scripts create a repository-local `.venv`, install `requirements.lock.txt`, run `npm ci`, generate an ignored development environment file, and check the documented ports. Manual equivalents are in [docs/local-development.md](docs/local-development.md).

## Change discipline

1. Add a failing test for behavior changes.
2. Implement the smallest coherent change.
3. Run focused tests and the related regression suite.
4. Update documentation when a call chain, dependency, operational assumption, or boundary changes.
5. Use clear commits such as `fix: reject stale assessment results` or `docs: clarify provider setup`.

Tests and fixtures must use tracked repository files. They may not read ignored Skill exports, user-level plugin caches, another clone's virtual environment, or another Compose project's volumes.

## Full verification

Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix web-ui test
npm --prefix web-ui run build
npm --prefix browser-extension/job-library test
docker compose --env-file .env.quickstart config --quiet
```

Linux or macOS:

```bash
.venv/bin/python -m pytest -q
npm --prefix web-ui test
npm --prefix web-ui run build
npm --prefix browser-extension/job-library test
docker compose --env-file .env.quickstart config --quiet
```

Paid or authenticated Provider tests belong in the manual Provider workflow. Every required pull-request check must run without external credentials.

## Pull requests

Include the user problem, implementation boundary, notable tradeoffs, commands/results, screenshots for visible UI changes, migration notes for schema changes, and privacy/documentation impact.

By contributing, you agree that your contribution is licensed under the repository's Apache-2.0 license and that you have the right to submit it.
