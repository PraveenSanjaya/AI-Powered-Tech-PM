# AI-Powered Tech PM

A technical project manager's assistant. It tracks sprint progress, flags blockers with deterministic rules,
answers questions with **evidence-backed** summaries, and proposes project-management actions (for example a Jira
comment) that a human must approve before anything is executed.

> **Status: Stage 1 (working application in demo mode).** Authentication, persistence, dashboards, risk detection,
> assistant, action approval/execution and audit all work end to end against **fictional demo data and simulated
> connectors**. Live GitHub, Jira, Slack, Google Calendar and Notion connectors (Stages 2–4) are **not implemented**.
> See [Current limitations](#current-limitations).

## Implemented features

- Local accounts (Argon2 password hashing), HttpOnly SameSite=Lax session cookie, CSRF header on all
  cookie-authenticated mutations, login rate limiting.
- Workspaces with `admin` / `manager` / `member` roles enforced in the backend on every route and tool call;
  strict workspace scoping (guessing IDs from another workspace returns 404).
- Projects, sprints, issues, pull requests (reviews + checks), external identities (never linked by display name),
  evidence records with provider URLs and timestamps, idempotent imports with unique constraints.
- Deterministic risk rules with editable thresholds, confidence labels (`confirmed` / `likely` / `possible`),
  de-duplicated findings, acknowledge/resolve/dismiss.
- Assistant workflow (coordinator → evidence collector → risk analyst → report writer → action planner) over typed
  tools with call budgets, timeouts, cancellation and visible workflow runs. Citations are validated against the
  retrieved evidence set. Works with no API key (template mode, clearly labelled); optional Anthropic provider
  rewrites the drafted answer and falls back to the template on failure or bad citations.
- Sprint summaries (overall status, done, in progress, confirmed blockers, possible risks, PR review needs, next
  actions, evidence links, freshness).
- Action state machine: draft → pending approval → approved → executing → succeeded, plus rejected / expired /
  failed / unknown outcome. Approval binds to an immutable payload version and hash; editing invalidates approval;
  execution rechecks role, connection capability, expiry and payload; retries are idempotent and uncertain outcomes
  are reconciled with the provider before any retry.
- Scheduled workflows (daily summary, blocker scan, weekly review) with timezone support (IANA, e.g. `Asia/Colombo`),
  slot de-duplication, run history, and report-only / draft-only behaviour unless an admin creates an explicit
  external-posting policy.
- Audit history, encrypted credential storage (Fernet, key from the environment), log redaction, readiness/liveness
  endpoints, Docker Compose stack (web, api, worker, scheduler, PostgreSQL, Redis).
- 12 UI pages: login/register, workspace & project selection, dashboard, sprint detail, issues, pull requests,
  risks & blockers, AI assistant, action approvals, integrations, automation, audit history.

## Architecture

```
apps/web         Next.js (TypeScript, Tailwind, TanStack Query, Recharts). Same-origin /api proxy to the backend.
services/api     FastAPI + SQLAlchemy + Alembic. Owns authz, tools, credentials, sync, approvals.
  app/api          HTTP routes          app/services   risk, summary, importer, sync, actions, audit
  app/auth         sessions, CSRF, RBAC app/workflows  assistant roles
  app/models       SQLAlchemy models    app/tools      typed tool registry
  app/integrations connector contract + demo connector  app/jobs  Celery tasks and schedule logic
infra/docker     Dockerfiles          docs/          architecture, integrations, security, testing, demo guide
```

Details: [docs/architecture.md](docs/architecture.md), [docs/security.md](docs/security.md),
[docs/integrations.md](docs/integrations.md).

## Prerequisites

- Docker Desktop (for the one-command path), **or** Python 3.13, Node.js 22+, PostgreSQL 16 and Redis 7 for local
  development.

## Quick start (Docker)

```bash
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD and CREDENTIAL_ENCRYPTION_KEY (command is in the file)
docker compose up --build
# in a second terminal — REQUIRED once, loads the fictional demo data and demo accounts:
docker compose exec api python -m app.cli seed
```

Open <http://localhost:3000>. API docs: <http://localhost:8000/api/docs>. Migrations run automatically when the
`api` container starts; the seed step is separate and development-only (it refuses to run if `APP_ENV=production`).

### Demo credentials (development only)

| Email | Role | Password |
| --- | --- | --- |
| `admin@demo.example` | admin | `demo-password-123` |
| `manager@demo.example` | manager | `demo-password-123` |
| `member@demo.example` | member | `demo-password-123` |

### Demo-mode limitations

All data is fictional. Evidence links use `https://demo.invalid/...` and do not resolve. "Executing" an approved
action writes to a local simulation ledger (`simulated_external_actions`); **nothing is sent to Jira, Slack or any
real service**, and the UI says so. Only Jira, GitHub and Slack *demo* connectors exist.

## Local development without Docker

```bash
# API
cd services/api
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://techpm:techpm@localhost:5432/techpm
export CREDENTIAL_ENCRYPTION_KEY=<generated Fernet key>
python -m app.cli migrate
python -m app.cli seed
uvicorn app.main:app --reload --port 8000
# optional background jobs
celery -A app.jobs.celery_app.celery worker --loglevel=INFO
celery -A app.jobs.celery_app.celery beat --loglevel=INFO

# Web (another terminal)
cd apps/web && npm install && npm run dev            # proxies /api to http://localhost:8000
```

## Environment variables

See `.env.example`. Key settings: `DATABASE_URL`, `REDIS_URL`, `CREDENTIAL_ENCRYPTION_KEY` (required),
`APP_ENV`, `DEMO_MODE`, `CORS_ORIGINS`, `COOKIE_SECURE`, `LLM_PROVIDER` (`demo` | `anthropic`), `LLM_MODEL`,
`ANTHROPIC_API_KEY`, `API_INTERNAL_URL` (web → api proxy target). Workflow limits (`WORKFLOW_MAX_TOOL_CALLS`,
`WORKFLOW_TIMEOUT_SECONDS`, `LLM_TIMEOUT_SECONDS`) and auth limits (`LOGIN_MAX_ATTEMPTS`, `LOGIN_WINDOW_SECONDS`)
are also configurable.

## Running the demo scenario

See [docs/demo-guide.md](docs/demo-guide.md): log in, open the dashboard, ask “What threatens our sprint goal?”,
generate a summary, prepare a Jira comment, inspect and approve the exact text, execute through the demo connector,
check the audit record, and retry to confirm no duplicate is created.

## Tests

```bash
# Backend (needs a disposable PostgreSQL; see docs/testing.md)
docker run -d --name techpm-test-pg -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=techpm_test -p 5433:5432 postgres:16-alpine
cd services/api && python -m pytest tests -q

# Frontend
cd apps/web
npm run typecheck && npm run lint && npm run build
# Browser tests need a second disposable database `techpm_e2e` and `npx playwright install chromium`
npm run test:e2e
```

## Troubleshooting

- `docker compose` complains about `POSTGRES_PASSWORD` / `CREDENTIAL_ENCRYPTION_KEY`: copy `.env.example` to `.env`
  and set both.
- Login works but the project list is empty: run the seed command (or an admin can use *Load fictional demo project*).
- `CryptoError: CREDENTIAL_ENCRYPTION_KEY is invalid`: the key must be a Fernet key (32 url-safe base64 bytes).
- Browser shows “Could not load data”: check `docker compose ps` and `http://localhost:8000/readyz`.
- Tests fail to connect to PostgreSQL: the test DB must be reachable at `TEST_DATABASE_URL`
  (default `postgresql+psycopg://test:test@localhost:5433/techpm_test`). Tests **drop and recreate the `public` schema**
  of that database; never point them at data you care about.

## Current limitations

- **No live integrations.** GitHub, Jira, Slack, Google Calendar and Notion connectors, OAuth/PKCE flows and
  webhook handling are not implemented. The connector interface (`app/integrations/base.py`) and the importer are
  built so they can be added; see [docs/integrations.md](docs/integrations.md). Pagination, rate-limit and
  retry behaviour therefore have no connector tests yet.
- Calendar availability and meeting creation are not available (tools return an explicit “not implemented”).
- Slack channel reading and blocker detection from chat work only on demo messages.
- The rate limiter is in-process (single API instance). A process crash mid-execution can leave an action in
  `executing`; there is no automatic sweeper yet.
- Velocity/burndown are intentionally empty until historical snapshots are collected.
- Memory: preferences/decisions/terminology are stored and listable via API; they are not yet fed into answers.
- Playwright and Docker Compose results are reported in the delivery notes; they depend on your environment.
- This is not production-ready: no HTTPS termination, no multi-instance rate limiting, no backup strategy, no
  external security review.
