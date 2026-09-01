# Contributing to ZhiTan

Thank you for helping improve ZhiTan. Small, focused changes with clear evidence are the easiest to review and maintain.

## Before you start

- Search existing issues and pull requests.
- Open an issue before a large feature, schema change, new external service, or user-facing workflow change.
- Do not include real resumes, interview transcripts, account data, cookies, server addresses, or credentials in issues, fixtures, screenshots, commits, or logs.
- Read [SAFETY.md](SAFETY.md) before changing browser automation, interview support, or assessment features.

## Development setup

Use Python 3.11 or newer and Node.js 22.

```bash
python -m venv .venv
python -m pip install -r requirements.txt -r requirements-career-assistant.txt -r requirements-development.txt
npm --prefix web-ui ci
cp .env.career-assistant.example .env.career-assistant
docker compose --env-file .env.career-assistant -f docker-compose.career-assistant.yml up -d
python -m alembic upgrade head
```

Never put a real secret in an example environment file. Local `.env*` files are ignored.

## Making a change

1. Keep one pull request focused on one problem.
2. Add a failing test for behavior changes, then implement the smallest coherent fix.
3. Preserve existing module boundaries. Do not couple the independent content workflow to the career database without an approved design.
4. Update the relevant `docs/` module note when a call chain, dependency, operational assumption, or boundary changes.
5. Use clear commit messages such as `fix: reject stale assessment results` or `docs: clarify provider setup`.

## Verification

Run focused tests while developing, then run the full offline baseline before opening a pull request:

```bash
python -m pytest -q
npm --prefix web-ui test
npm --prefix web-ui run build
npm --prefix browser-extension/job-library test
```

If a test requires a paid or authenticated external service, explain that requirement and provide an offline contract test. Do not weaken assertions to hide a failure.

## Pull requests

A useful pull request includes:

- the user problem and intended outcome;
- the implementation boundary and notable tradeoffs;
- test commands and results;
- screenshots for visible UI changes;
- migration and rollback notes for schema changes;
- privacy, safety, and documentation impact;
- a linked issue when one exists.

Maintainers may ask to split broad changes. Reviews check both repository conventions and the stated problem, not only whether the code runs.

## Licensing

By submitting a contribution, you agree that it is licensed under the repository's Apache-2.0 license. You must have the right to contribute the code and must preserve required third-party notices.
