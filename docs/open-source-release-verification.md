# Open-source release verification

This record describes the checks required before publishing a ZhiTan release. It is intentionally limited to reproducible repository and clean-room installation evidence; it does not certify third-party AI providers or production infrastructure.

## Verification environment

- Date: 2026-09-02
- Host: Windows 11, Docker Desktop, PowerShell
- Branch: `master`
- Runtime: Python 3.13, Node.js 22, Docker Compose v2
- Isolated Compose project: `zhitanosscheck`
- Public entry point: `http://127.0.0.1:18081`

## Repository checks

| Check | Result |
| --- | --- |
| Python test suite | 647 passed; 4 deprecation warnings |
| Web UI test suite | 229 passed |
| Browser extension test suite | 34 passed |
| Web UI production build | Passed; Vite reported the documented chunk-size warning |
| Gitleaks full-history scan | Passed; no leaks detected |
| Tracked-file/public-checkout contract | Included in the Python suite and passed |

## Quickstart checks

The quickstart environment was generated from the tracked example without overwriting an existing file. The complete five-service stack was then built and started with an isolated Compose project name.

| Check | Result |
| --- | --- |
| PostgreSQL health check | Healthy |
| Alembic migration job | Exited successfully with code 0 |
| API readiness (`/api/ready`) | Ready |
| Durable worker health check | Healthy, no unexpected restart |
| Web reverse proxy health check | Healthy |
| First-run bootstrap state | Correctly reported as pending before account creation |
| Anonymous protected API request | Correctly rejected with HTTP 401 |
| Ephemeral smoke-test admin | Created only with the explicit test-mode guard |
| Password login and `/api/auth/me` | Passed through the public Web entry point |
| Relevant service logs | No Python traceback detected |
| Host-side verifier dependencies | Python standard library only; starts with site packages disabled |

## Isolation checks

- Only the Web service published a host port; PostgreSQL and backend services remained on the private Compose network.
- The validation stack used project-scoped container, network, and volume names.
- Existing `wechat-agent-*` containers and volumes remained running and were not recreated.
- No production deployment was performed.

## Release boundary

The core quickstart is provider-independent. Real LLM, Firecrawl, email, document-processing, audio, and video integrations require separate credentials or optional profiles and are not part of the offline login smoke test. CI repeats the repository checks and a Linux clean-room quickstart for every change to `master`.
