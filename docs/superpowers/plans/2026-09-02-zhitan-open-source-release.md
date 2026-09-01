# ZhiTan Open-Source Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a clean, tested ZhiTan snapshot on `HBelike/ZhiTan` while removing only the Resume Assistant and Evaluation Center page routes and keeping their implementation code.

**Architecture:** Work only in the isolated `<zhitan-worktree>` repository on `master`. Remove the two features from route catalogs and rendering while retaining components and backend modules; move deployment-specific identity and origin values behind environment/package configuration; sanitize the public tree before its first push.

**Tech Stack:** Python 3.11+、FastAPI、SQLAlchemy/Alembic、Vue 3、Vite 7、Chrome Extension Manifest V3、Docker Compose、pytest、Node test runner、GitHub Actions

**Spec:** `docs/superpowers/specs/2026-09-02-zhitan-open-source-release-design.md`

## Global Constraints

- Only modify `<zhitan-worktree>`; `<original-worktree>` remains read-only.
- Work and commit only on `master`; the only remote is `git@github.com:HBelike/ZhiTan.git`.
- Remove only the page routes/navigation for Resume Assistant and Evaluation Center; retain their components, styles, APIs, services, repositories, migrations, tests, and design documents.
- Brand text is always `ZhiTan`; there is no Chinese product name.
- Do not commit real environment files, runtime data, user attachments, logs, generated media, dependency caches, API keys, personal email addresses, or real server identifiers.
- Do not connect to or deploy to the existing production server.
- Use Apache-2.0 for original project code and preserve third-party license notices.

---

### Task 1: Unmount the two page routes without deleting their implementations

**Files:**
- Modify: `web-ui/src/navigation-access.test.js`
- Modify: `tests/test_navigation_config.py`
- Modify: `web-ui/src/App.vue`
- Modify: `web-ui/src/navigation-access.js`
- Modify: `src/platform_access/navigation_config.py`

**Interfaces:**
- Consumes: `normalizeAppRoute(pathname, reviewRoutes) -> string` and `route_modules_for_ui(value, role) -> list[dict]`.
- Produces: legacy `/resume-assistant` and `/evaluations` URLs normalize to `/career`; the public route catalog has seven top-level routes and two feature switches.

- [x] **Step 1: Add failing route-removal tests**

```javascript
test('已卸载页面地址统一回到求职助手', () => {
  assert.equal(normalizeAppRoute('/resume-assistant'), '/career')
  assert.equal(normalizeAppRoute('/resume-assistant/new'), '/career')
  assert.equal(normalizeAppRoute('/evaluations'), '/career')
  assert.equal(normalizeAppRoute('/evaluations/history'), '/career')
})
```

```python
def test_retired_routes_are_not_returned_but_legacy_settings_are_accepted(self) -> None:
    modules = route_modules_for_ui(
        {"resume_assistant": False, "evaluation_center": False},
        PlatformRole.ADMIN,
    )
    keys = {item["key"] for item in modules}
    self.assertNotIn("resume_assistant", keys)
    self.assertNotIn("evaluation_center", keys)
    self.assertEqual(len([item for item in modules if item["scope"] == "route"]), 7)
```

- [x] **Step 2: Run the focused tests and confirm failure**

```powershell
python -m pytest tests/test_navigation_config.py -q
Set-Location web-ui
node --test src/navigation-access.test.js
```

Expected: assertions fail because both routes and catalog entries still exist.

- [x] **Step 3: Remove only route and navigation mounting**

In `App.vue`, remove both page imports, the two `appNavItems`, metadata branches, route CSS flags, and template branches. Do not delete either component file.

In `navigation-access.js`, delete the two explicit normalization branches.

In `navigation_config.py`, remove the two definitions and add compatibility handling:

```python
RETIRED_ROUTE_MODULE_KEYS = frozenset({"resume_assistant", "evaluation_center"})

accepted_keys = {
    *DEFAULT_ROUTE_MODULE_SETTINGS,
    *RETIRED_ROUTE_MODULE_KEYS,
    LEGACY_CAREER_TOOLS_KEY,
}

for key, enabled in source.items():
    if key == LEGACY_CAREER_TOOLS_KEY or key in RETIRED_ROUTE_MODULE_KEYS:
        continue
```

- [x] **Step 4: Run route tests and confirm pass**

Run the two commands from Step 2. Expected: both focused suites pass; route mounting is absent while both component files still exist.

- [x] **Step 5: Commit the route change**

```powershell
git add web-ui/src/App.vue web-ui/src/navigation-access.js web-ui/src/navigation-access.test.js src/platform_access/navigation_config.py tests/test_navigation_config.py
git commit -m "refactor: unmount retired product routes"
```

---

### Task 2: Move the administrator email out of source and database policy

**Files:**
- Create: `src/platform_access/settings.py`
- Create: `tests/test_platform_access_settings.py`
- Modify: `src/platform_access/contracts.py`
- Modify: `migrations/versions/20260825_20_single_platform_admin.py`
- Modify: `migrations/versions/20260828_29_assign_default_career_history_to_admin.py`
- Modify: `tests/test_single_platform_admin_migration.py`
- Modify: `tests/test_default_career_ownership_migration.py`
- Modify: `tests/test_navigation_config.py`
- Modify: `.env.example`
- Modify: `.env.career-assistant.example`
- Modify: `.env.production.example`

**Interfaces:**
- Produces: `load_platform_admin_email(environ: Mapping[str, str] | None = None) -> str` and the existing `PLATFORM_ADMIN_EMAIL: str`, now environment-backed.
- Consumes: existing bootstrap/service/repository imports of `PLATFORM_ADMIN_EMAIL`; no call-site signature changes.

- [x] **Step 1: Add failing configuration and migration tests**

```python
def test_platform_admin_email_comes_from_environment() -> None:
    assert load_platform_admin_email({"PLATFORM_ADMIN_EMAIL": " Admin@Example.COM "}) == "admin@example.com"


def test_platform_admin_email_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="PLATFORM_ADMIN_EMAIL"):
        load_platform_admin_email({"PLATFORM_ADMIN_EMAIL": "not-an-email"})
```

Update migration tests to assert that migration 20 contains `uq_platform_users_single_admin` but not `platform_users_admin_email_check`, and migration 29 selects one active `role = 'admin'` without an email literal.

- [x] **Step 2: Run focused tests and confirm failure**

```powershell
python -m pytest tests/test_platform_access_settings.py tests/test_single_platform_admin_migration.py tests/test_default_career_ownership_migration.py tests/test_navigation_config.py -q
```

Expected: the settings module is missing and migration assertions fail.

- [x] **Step 3: Implement the environment-backed setting**

```python
from collections.abc import Mapping
import os

DEFAULT_PLATFORM_ADMIN_EMAIL = "admin@example.com"


def load_platform_admin_email(environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    value = source.get("PLATFORM_ADMIN_EMAIL", DEFAULT_PLATFORM_ADMIN_EMAIL).strip().casefold()
    local, separator, domain = value.partition("@")
    if not local or separator != "@" or "." not in domain:
        raise ValueError("PLATFORM_ADMIN_EMAIL 必须是有效邮箱")
    return value
```

Set `PLATFORM_ADMIN_EMAIL = load_platform_admin_email()` in `contracts.py`. In migration 20, preserve at most one pre-existing administrator by role, remove the email-specific constraint, and retain the partial unique admin index. In migration 29, identify the single active administrator by role only.

Migration 20 must normalize and deduplicate deterministically before creating the partial unique index:

```sql
UPDATE platform_users
SET role = 'user'
WHERE role <> 'admin';

WITH ranked_admins AS (
    SELECT id,
           ROW_NUMBER() OVER (ORDER BY created_at ASC, id ASC) AS position
    FROM platform_users
    WHERE role = 'admin'
)
UPDATE platform_users AS users
SET role = 'user'
FROM ranked_admins
WHERE users.id = ranked_admins.id
  AND ranked_admins.position > 1;
```

The downgrade removes only `uq_platform_users_single_admin`; it must not recreate the private email-specific constraint. Migration 29 selects the first active administrator with `WHERE role = 'admin' AND is_active IS TRUE ORDER BY created_at ASC, id ASC LIMIT 1`.

Add `PLATFORM_ADMIN_EMAIL=admin@example.com` to all three example environments.

- [x] **Step 4: Run administrator tests**

```powershell
python -m pytest tests/test_platform_access_settings.py tests/test_single_platform_admin_migration.py tests/test_default_career_ownership_migration.py tests/test_navigation_config.py tests/test_platform_actor_middleware.py -q
```

Expected: all focused tests pass and no private administrator address exists in code or migrations.

- [x] **Step 5: Commit administrator configuration**

```powershell
git add src/platform_access/settings.py src/platform_access/contracts.py migrations/versions/20260825_20_single_platform_admin.py migrations/versions/20260828_29_assign_default_career_history_to_admin.py tests/test_platform_access_settings.py tests/test_single_platform_admin_migration.py tests/test_default_career_ownership_migration.py tests/test_navigation_config.py .env.example .env.career-assistant.example .env.production.example
git commit -m "refactor: configure platform administrator by environment"
```

---

### Task 3: Make the packaged browser extension origin configurable

**Files:**
- Create: `tests/test_package_boss_extension.py`
- Modify: `scripts/package_boss_extension.py`
- Modify: `browser-extension/job-library/manifest.json`
- Modify: `browser-extension/job-library/service-worker.js`
- Modify: `browser-extension/job-library/README.md`
- Modify: `web-ui/src/components/BossExtensionInstallDialog.vue`
- Modify: `.env.production.example`

**Interfaces:**
- Produces: `normalize_app_origin(value: str | None) -> str | None`, `build_manifest(app_origin: str | None) -> dict`, and `render_service_worker(app_origin: str | None) -> bytes`.
- Consumes: optional `ZHITAN_APP_ORIGIN`, for example `https://jobs.example.com`; source-unpacked extension continues to support localhost.

- [x] **Step 1: Add failing package tests**

```python
def test_package_origin_is_added_without_private_domain() -> None:
    manifest = build_manifest("https://jobs.example.com")
    assert "https://jobs.example.com/*" in manifest["host_permissions"]
    worker = render_service_worker("https://jobs.example.com").decode("utf-8")
    assert 'const PACKAGED_APP_ORIGIN = "https://jobs.example.com"' in worker


def test_invalid_package_origin_is_rejected() -> None:
    with pytest.raises(ValueError, match="https"):
        normalize_app_origin("ftp://example.com")
```

- [x] **Step 2: Run the test and confirm failure**

```powershell
python -m pytest tests/test_package_boss_extension.py -q
```

Expected: imports fail because the helper functions do not exist.

- [x] **Step 3: Implement source-local defaults and package-time injection**

Keep only localhost app origins in source `manifest.json`. Add to `service-worker.js`:

```javascript
const PACKAGED_APP_ORIGIN = ''
const DEFAULT_APP_ORIGIN = PACKAGED_APP_ORIGIN || 'http://127.0.0.1'
```

Build `ALLOWED_APP_HOSTS`, `APP_PAGE_PATTERNS`, and the assessment fallback from `DEFAULT_APP_ORIGIN`. In the packager, normalize `ZHITAN_APP_ORIGIN`, add its host pattern to the packaged manifest, and replace only the exact `PACKAGED_APP_ORIGIN` declaration in the packaged worker bytes.

Document the packaging command:

```powershell
$env:ZHITAN_APP_ORIGIN = 'https://jobs.example.com'
python scripts/package_boss_extension.py
```

- [x] **Step 4: Run package and extension tests**

```powershell
python -m pytest tests/test_package_boss_extension.py tests/test_boss_extension_distribution.py -q
npm --prefix browser-extension/job-library test
```

Expected: package tests and extension tests pass; tracked extension sources contain no deployment-specific domain.

- [x] **Step 5: Commit origin configuration**

```powershell
git add tests/test_package_boss_extension.py scripts/package_boss_extension.py browser-extension/job-library/manifest.json browser-extension/job-library/service-worker.js browser-extension/job-library/README.md web-ui/src/components/BossExtensionInstallDialog.vue .env.production.example
git commit -m "refactor: configure extension application origin"
```

---

### Task 4: Apply ZhiTan branding and sanitize historical documentation

**Files:**
- Modify: `web-ui/index.html`
- Modify: `web-ui/package.json`
- Modify: `web-ui/package-lock.json`
- Modify: `config/app.yaml`
- Modify: `docker-compose.production.yml`
- Modify: `browser-extension/job-library/manifest.json`
- Modify: `src/providers/doubao_tts_provider.py`
- Modify: `tests/test_langsmith_runtime.py`
- Modify: all copied `docs/**/*.md` files containing private deployment identifiers

**Interfaces:**
- Produces: public brand `ZhiTan`, package identifiers `zhitan-ui`/`zhitan`, and documentation-only example identities.
- Consumes: no runtime interface changes beyond names shown in logs, HTML, Compose labels, and package metadata.

- [x] **Step 1: Record the failing sanitization scan**

```powershell
rg -n '2963613812@qq\.com|xxlwcc@gmail\.com|43\.155\.86\.239|xingxingtech\.cn|ZhiTan|ZhiTan|sk-[A-Za-z0-9_-]{16,}' .
```

Expected: matches occur in code, tests, extension sources, and historical documentation.

- [x] **Step 2: Replace private identifiers with deterministic examples**

Use these exact substitutions across documentation and fixtures:

```text
admin@example.com       -> admin@example.com
user@example.com        -> user@example.com
private deployment IP  -> 203.0.113.10
your-domain.example         -> your-domain.example
/opt/zhitan -> /opt/zhitan
HBelike/ZhiTan -> HBelike/ZhiTan
test-langsmith-key   -> test-langsmith-key
```

Do not replace third-party URLs, protocol examples, or references needed for license attribution.

- [x] **Step 3: Apply public brand identifiers**

```text
web-ui HTML title              = ZhiTan
web-ui npm package             = zhitan-ui
config app.name                = zhitan
Docker Compose project name    = zhitan
browser extension display name = ZhiTan Browser Assistant
Doubao TTS request uid         = zhitan
```

- [x] **Step 4: Run the sanitization scan and syntax checks**

```powershell
rg -n '2963613812@qq\.com|xxlwcc@gmail\.com|43\.155\.86\.239|xingxingtech\.cn|ZhiTan|ZhiTan|sk-[A-Za-z0-9_-]{16,}' .
python -m compileall src scripts migrations
npm --prefix web-ui test
```

Expected: the sensitive scan returns no matches; compileall and Node tests pass.

- [x] **Step 5: Commit brand and sanitization changes**

```powershell
git add web-ui/index.html web-ui/package.json web-ui/package-lock.json config/app.yaml docker-compose.production.yml browser-extension/job-library/manifest.json src/providers/doubao_tts_provider.py tests/test_langsmith_runtime.py docs
git commit -m "chore: apply ZhiTan brand and sanitize public docs"
```

---

### Task 5: Create the public README and governance documents

**Files:**
- Create: `README.md`
- Create: `README.zh-CN.md`
- Create: `LICENSE`
- Create: `CONTRIBUTING.md`
- Create: `CODE_OF_CONDUCT.md`
- Create: `SECURITY.md`
- Create: `SAFETY.md`
- Create: `THIRD_PARTY_NOTICES.md`
- Modify: `docs/open_source_attribution.md`

**Interfaces:**
- Produces: public installation, architecture, contribution, vulnerability reporting, responsible-use, and licensing contracts.
- Consumes: verified commands and existing architecture in `CLAUDE.md`, Compose files, `requirements*.txt`, and `web-ui/package.json`.

- [x] **Step 1: Write the English README with concrete commands**

```markdown
# ZhiTan

Open-source AI job-search and resume intelligence workbench.

[简体中文](README.zh-CN.md)

## What ZhiTan does
## Product modules
## Quickstart with Docker Compose
## Local development
## Architecture
## Model and service providers
## Privacy and responsible use
## Contributing
## License
```

Quickstart uses `.env.production.example`, `docker-compose.production.yml`, `/api/health`, `/career`, and exact repository-supported prerequisites.

- [x] **Step 2: Write the Chinese README with the same claims and commands**

Use the same section order and link back with `[English](README.md)`. Keep `ZhiTan` as the only product name and explain that Resume Assistant and Evaluation Center source remains but their pages are not mounted.

- [x] **Step 3: Add legal and community policies**

Use the unmodified Apache License 2.0 text in `LICENSE`. `CONTRIBUTING.md` documents setup, focused/full tests, commit/PR expectations, and the no-real-credentials rule. `SECURITY.md` directs reports to GitHub private vulnerability reporting without inventing an email. `SAFETY.md` prohibits unauthorized automated applications, credential capture, impersonation, and misuse of interview/assessment assistance.

- [x] **Step 4: Build third-party notices from repository evidence**

Include Noto CJK under SIL Open Font License 1.1 and the five projects already listed in `docs/open_source_attribution.md`. State that dependency licenses remain governed by upstream packages; do not claim copied code where only design ideas were reviewed.

- [x] **Step 5: Check links and commit**

```powershell
rg -n '\]\([^)]*\)' README.md README.zh-CN.md CONTRIBUTING.md SECURITY.md SAFETY.md THIRD_PARTY_NOTICES.md
git diff --check
git add README.md README.zh-CN.md LICENSE CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md SAFETY.md THIRD_PARTY_NOTICES.md docs/open_source_attribution.md
git commit -m "docs: add ZhiTan open-source documentation"
```

Expected: every local link target exists and Git reports no whitespace errors.

---

### Task 6: Add GitHub collaboration templates and baseline CI

**Files:**
- Create: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Create: `.github/ISSUE_TEMPLATE/feature_request.yml`
- Create: `.github/ISSUE_TEMPLATE/config.yml`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: structured issue intake, PR verification checklist, and Python/Node CI jobs.
- Consumes: Python 3.12, Node 22, `requirements.txt`, `requirements-career-assistant.txt`, `requirements-development.txt`, and `web-ui/package-lock.json`.

- [x] **Step 1: Create issue and PR templates**

Bug reports require environment, reproduction, expected behavior, actual behavior, and sanitized logs. Feature requests require user problem, proposed outcome, alternatives, and scope. PRs require a linked issue, change summary, test evidence, privacy check, and documentation impact.

- [x] **Step 2: Create CI with isolated backend and frontend jobs**

```yaml
name: CI
on:
  push:
    branches: [master]
  pull_request:
    branches: [master]
jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt -r requirements-career-assistant.txt -r requirements-development.txt
      - run: python -m pytest -q
  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web-ui
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: web-ui/package-lock.json
      - run: npm ci
      - run: npm test
      - run: npm run build
```

- [x] **Step 3: Validate YAML and referenced frontend commands**

```powershell
python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text(encoding='utf-8')); print('workflow yaml ok')"
npm --prefix web-ui test
npm --prefix web-ui run build
```

Expected: YAML parses, tests pass, and Vite produces `web-ui/dist`.

- [x] **Step 4: Commit GitHub configuration**

```powershell
git add .github
git commit -m "ci: add GitHub collaboration checks"
```

---

### Task 7: Import the remaining clean snapshot, verify, and publish `master`

**Files:**
- Modify: `.gitignore`
- Add: all remaining project files not excluded by `.gitignore`

**Interfaces:**
- Produces: complete public repository history and remote `master`.
- Consumes: all prior commits and the clean working tree.

- [x] **Step 1: Strengthen ignore rules before staging**

```gitignore
.env
.env.*
!.env.example
!.env.*.example
.venv/
data/
logs/
outputs/
.firecrawl/
**/node_modules/
web-ui/dist/
*.pem
*.key
*.p12
*.pfx
```

Keep `assets/fonts/NotoSansSC-VF.ttf` because its license is included.

- [x] **Step 2: Run focused and full local verification**

```powershell
python -m pytest tests/test_navigation_config.py tests/test_platform_access_settings.py tests/test_single_platform_admin_migration.py tests/test_default_career_ownership_migration.py tests/test_package_boss_extension.py -q
python -m pytest -q
npm --prefix web-ui ci
npm --prefix web-ui test
npm --prefix web-ui run build
npm --prefix browser-extension/job-library test
```

Expected: all offline tests and frontend build pass. Record any pre-existing external-service failure with its exact test name; do not hide it by changing assertions.

- [x] **Step 3: Stage all remaining files and scan the actual index**

```powershell
git add --all
git diff --cached --check
git grep --cached -n -I -E 'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----|sk-[A-Za-z0-9_-]{16,}|2963613812@qq\.com|43\.155\.86\.239|xingxingtech\.cn|ZhiTan'
git status --short
```

Expected: whitespace check passes, sensitive scan returns no matches, and ignored runtime directories are absent from the index.

- [x] **Step 4: Commit the complete clean snapshot**

```powershell
git commit -m "chore: import clean ZhiTan project snapshot"
```

- [x] **Step 5: Confirm isolation before network mutation**

```powershell
git branch --show-current
git remote -v
git status --short
git -C <original-worktree> status --short
```

Expected: ZhiTan is clean on `master`, has only the ZhiTan remote, and the original repository contains no task-created tracked changes.

- [x] **Step 6: Push and verify the public repository**

```powershell
git push -u origin master
git ls-remote --heads origin master
```

Query `https://api.github.com/repos/HBelike/ZhiTan` and confirm the repository is public, non-empty, and reports `master` as the default branch. If GitHub retains `main`, change the default branch to `master` through authenticated GitHub settings; do not force-push unrelated refs.

- [x] **Step 7: Record final evidence**

Append a verification section to this plan with commit SHA, test totals, build result, scan result, remote branch, and any known non-blocking limitation. Commit and push that documentation update.

## Verification record

- Date: 2026-09-02 (Asia/Shanghai)
- Clean snapshot commit: `853848abdfd60e4ef366a7840ffab43310372220`
- Python: 609 passed; 5 dependency deprecation warnings
- Web UI: 226 passed
- Browser extension: 34 passed
- Production frontend build: passed with Vite 7.3.6
- Sensitive-data scan: no known private identifiers or credential-shaped tokens in the Git index
- Commit identity scan: every commit uses `HBelike@users.noreply.github.com`
- Public repository: `https://github.com/HBelike/ZhiTan`
- Remote branch: `master`; GitHub default branch: `master`
- License boundary: vendor-managed Skill cache exports are ignored and not published; the generic Skill management code remains available
- Known non-blocking limitation: Vite reports one minified application chunk larger than 500 kB
