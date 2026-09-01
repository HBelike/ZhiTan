# Xiaohongshu Browser Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build keyword-driven Xiaohongshu card discovery and per-note interview-library ingestion through the existing logged-in Chrome assistant.

**Architecture:** The Chrome MV3 extension reads search cards and note details in the user's Xiaohongshu session. The Web UI serializes browser reads and submits sanitized discoveries/details to FastAPI, while the backend reuses the existing temporary image, OCR, evidence analysis, ingest, and RAG pipeline.

**Tech Stack:** Chrome MV3 JavaScript, Vue 3/Vite, FastAPI/Pydantic, Python service layer, PostgreSQL JSONB metadata, Node test runner.

**Spec:** `docs/superpowers/specs/2026-08-26-xiaohongshu-browser-collection-design.md`

## Global Constraints

- PC Chrome only for the first release.
- Default 20 results; accept 5–50; no infinite scrolling or scheduled collection.
- Never persist cookies, passwords, `xsec_token`, original HTML, original image bytes, or image URLs.
- Reuse existing temporary image download, OCR, evidence analysis, ingest, and RAG code.
- Persist only sanitized text, canonical `/explore/{note_id}` source URL, derived metadata, statuses, and errors.
- Stop the batch on login, verification, or explicit rate limiting; continue on isolated note/image failures.
- Preserve unrelated dirty-worktree changes and do not deploy production.

---

### Task 1: Browser-side Xiaohongshu adapter

**Files:**
- Create: `browser-extension/job-library/xiaohongshu-data.js`
- Create: `browser-extension/job-library/xiaohongshu-page.js`
- Create: `browser-extension/job-library/tests/xiaohongshu-data.test.mjs`
- Modify: `browser-extension/job-library/service-worker.js`
- Modify: `browser-extension/job-library/content-script.js`
- Modify: `browser-extension/job-library/manifest.json`

**Interfaces:**
- Produces: `search_xiaohongshu_notes({keyword, limit}) -> {keyword, cards[]}`.
- Produces: `get_xiaohongshu_note({noteId, signedUrl}) -> sanitized note detail`.
- Produces: extension capability `xiaohongshu_keyword_collection`.

- [ ] Write Node tests for search URL encoding, canonical note URLs, card normalization, page-state failure classification, detail normalization, and removal of token-bearing URLs.
- [ ] Run `npm --prefix browser-extension/job-library test` and verify the new tests fail because the adapter modules do not exist.
- [ ] Implement pure builders/normalizers in `xiaohongshu-data.js` and self-contained page-context readers in `xiaohongshu-page.js`.
- [ ] Add a dedicated inactive-tab lifecycle and both read-only actions to `service-worker.js`; activate the tab only for login/verification/rate-limit intervention.
- [ ] Add both actions to the Content Script allowlist, add the Xiaohongshu host permission, bump the extension version, and expose the capability through `ping`.
- [ ] Run extension tests plus `node --check browser-extension/job-library/service-worker.js` and `node --check browser-extension/job-library/xiaohongshu-page.js`.

### Task 2: Backend browser-result orchestration

**Files:**
- Modify: `src/career_assistant/interview_library/repository.py`
- Modify: `src/career_assistant/interview_library/collection.py`
- Modify: `src/career_assistant/web/router.py`
- Create: `scripts/verify_xiaohongshu_browser_collection.py`

**Interfaces:**
- Produces: `create_xiaohongshu_browser_collection_job(organization_id, keyword, requested_limit)`.
- Produces: `register_xiaohongshu_browser_discoveries(organization_id, job_id, discoveries)`.
- Produces: `process_xiaohongshu_browser_note(organization_id, job_id, note)` for BackgroundTasks.
- Produces: pause and complete service methods.

- [ ] Write a verification script with fake repository/OCR/analyzer/library dependencies that asserts `user_authorized_browser`, per-note deduplication, sanitized metadata, OCR reuse, conditional auto-import, pause, and completion.
- [ ] Run the verification script and confirm it fails on missing browser-collection methods.
- [ ] Add repository lookups by job/canonical URL and by organization/source URL without adding a migration.
- [ ] Add browser-job creation, discovery registration, existing-candidate processing, summary accounting, pause, and completion to `InterviewCollectionService`.
- [ ] Refactor `_process_xiaohongshu_note` to update a pre-created candidate when `candidate_id` is supplied while preserving the public URL import behavior.
- [ ] Add Pydantic request contracts and five browser-collection routes; reject token-bearing fields and schedule only the sanitized in-memory note detail in BackgroundTasks.
- [ ] Run the new script and existing Xiaohongshu collection/import/API verification scripts.

### Task 3: Web UI bridge and serial controller

**Files:**
- Create: `web-ui/src/xiaohongshu-collection.js`
- Create: `web-ui/src/xiaohongshu-collection.test.js`
- Modify: `web-ui/src/job-library-bridge.js`
- Modify: `web-ui/src/job-library-bridge.test.js`

**Interfaces:**
- Produces: `jobLibraryBridge.searchXiaohongshuNotes(keyword, limit)`.
- Produces: `jobLibraryBridge.getXiaohongshuNote(card)`.
- Produces: pure helpers for capability checks, sanitized discovery/detail payloads, terminal-candidate detection, resume filtering, and stoppable serial processing.

- [ ] Write failing tests for clone-safe browser payloads, removal of `signedUrl`/`xsec_token`, default/range limits, terminal candidate states, and resume selection by `note_id`.
- [ ] Run the focused Node tests and confirm the new exports are missing.
- [ ] Add bridge actions with collection-specific timeouts and plain-language extension errors.
- [ ] Implement pure collection helpers without Vue dependencies.
- [ ] Run the focused tests and the complete Web UI test suite.

### Task 4: Interview-library information collection UI

**Files:**
- Modify: `web-ui/src/components/InterviewLibraryPage.vue`
- Create: `web-ui/src/interview-library-xiaohongshu-browser.test.js`

**Interfaces:**
- Consumes: Task 2 browser-collection HTTP routes.
- Consumes: Task 3 bridge actions and serial/resume helpers.
- Produces: top-level “信息收集” entry and live keyword-collection task UI.

- [ ] Write a component contract test for the adjacent button, keyword/count fields, extension capability gate, local task ID, pause/complete endpoints, and Chinese status copy.
- [ ] Run the component test and verify it fails against the current public-search implementation.
- [ ] Add the adjacent action and repurpose keyword mode for the authorized browser connector while leaving URL import unchanged.
- [ ] Implement create -> search -> sanitized discovery -> sequential detail -> poll -> complete, plus user stop and automatic pause on account-level errors.
- [ ] Restore an unfinished task ID from local storage, re-search the keyword, and continue only `discovered` candidates.
- [ ] Render per-card status and existing manual-review controls; refresh the tree after automatic imports.
- [ ] Run the focused component test, full Web UI tests, and `npm --prefix web-ui run build`.

### Task 5: Distribution, documentation, and regression

**Files:**
- Modify: `browser-extension/job-library/README.md`
- Modify: `web-ui/src/boss-extension-onboarding.js`
- Modify: `web-ui/public/boss-extension-guide.html`
- Modify: `docs/xiaohongshu_interview_import.md`
- Modify: `docs/interview_library_collection_module.md`
- Create: `web-ui/public/downloads/find-job-boss-helper-v<new-version>.zip`

**Interfaces:**
- Produces: a version-aligned install archive containing both new Xiaohongshu modules.

- [ ] Update the extension README, module docs, capability boundary, call chain, failure rules, validation results, and local-only deployment status.
- [ ] Update the Web UI extension version and installation guide download URL.
- [ ] Update the deterministic packaging script file allowlist if needed and generate the versioned ZIP.
- [ ] Run distribution tests, extension tests, all focused backend verification scripts, the full relevant Python test suite, the full Web UI suite, and the production build.
- [ ] Inspect `git diff --check`, `git status --short`, and the final diff to confirm unrelated user changes remain intact and no token, Cookie, image bytes, or production mutation was introduced.

## Self-review

- Every confirmed requirement maps to Tasks 1–5.
- No database migration or third-party runtime is required.
- Browser interfaces use camelCase; HTTP request models use snake_case; the Web UI sanitizer is the only boundary between them.
- The plan contains no production deployment step and no operation that discards existing dirty-worktree changes.
