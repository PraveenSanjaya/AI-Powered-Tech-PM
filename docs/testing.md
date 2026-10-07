# Testing

## Backend (pytest, real PostgreSQL)

```bash
docker run -d --name techpm-test-pg -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=techpm_test -p 5433:5432 postgres:16-alpine
cd services/api
python -m pytest tests -q
```

The suite applies the **real Alembic migrations** to a freshly recreated `public` schema (so migration drift fails
tests) and truncates all tables between tests. It never sends external requests; actions run through the demo
connector or monkeypatched fakes.

Covered: authentication, CSRF, rate limiting, role enforcement, cross-workspace isolation, risk rules and
threshold boundaries, finding de-duplication/auto-resolve, import idempotency, checkpoint use, identity handling,
evidence-citation validation, LLM failure/fabricated-citation fallback, workflow timeout, tool budget and role
checks, prompt-injection inertness, approval invalidation, tampering, member/unauthorised execution, repeated
execution, uncertain provider outcomes (reconcile vs retry), calendar timezone validation, schedule timezone and
de-duplication, policy-scoped posting, secrets encryption/redaction, disconnect/purge, health endpoints.

Not covered (not implemented): connector pagination/rate-limit/retry, webhook signatures, live-provider calls.

Set `TEST_DATABASE_URL` to use another database. **Tests drop the `public` schema of that database.**

## Frontend

```bash
cd apps/web
npm run typecheck && npm run lint && npm run build
```

## Browser (Playwright)

Needs a disposable database `techpm_e2e` on the same server and a Chromium download:

```bash
docker exec techpm-test-pg psql -U test -d techpm_test -c "create database techpm_e2e"
cd apps/web && npx playwright install chromium && npm run test:e2e
```

`playwright.config.ts` starts the API (`e2e/start-api.mjs`: reset schema, migrate, seed, serve on :8000) and the
Next.js dev server on :3000. Override with `E2E_DATABASE_URL` / `E2E_PYTHON`.
Flows: login (bad + good), dashboard, issue/PR filters, assistant answer with evidence, draft → approve →
simulated execute → safe retry → audit, integration error and disconnected states.
