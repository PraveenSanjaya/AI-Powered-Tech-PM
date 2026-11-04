# Architecture

## Shape

One modular backend (FastAPI), one frontend (Next.js), PostgreSQL for all durable state, Redis + Celery for
background work. No microservices or extra agent frameworks.

The browser talks only to the Next.js origin. `/api/*` is proxied to the backend, so session cookies are
same-origin and provider credentials never leave the server.

## Backend modules

| Module | Responsibility |
| --- | --- |
| `app/auth` | Session cookies (hashed tokens in DB), CSRF check, role and workspace dependencies |
| `app/api` | HTTP routes; every workspace route depends on membership (404 for non-members) |
| `app/models` | SQLAlchemy models; migrations in `alembic/versions` |
| `app/integrations` | `Connector` contract, `ProviderError` normalisation, demo connector |
| `app/services/importer.py` | Idempotent upsert of normalised records (unique per workspace/provider/external id) |
| `app/services/sync.py` | Sync runs, checkpoints, connection status updates |
| `app/services/risk.py` | Deterministic rules and finding upsert/auto-resolve |
| `app/services/summary.py` | Evidence-backed sections, markdown rendering, citation resolution |
| `app/services/actions.py` | Action state machine, approval binding, idempotent execution |
| `app/tools/registry.py` | Typed tools, role checks, call budget, retrieved-evidence tracking |
| `app/workflows/assistant.py` | Coordinator, evidence collector, risk analyst, report writer, action planner |
| `app/agents/llm.py` | Provider abstraction (demo + Anthropic) |
| `app/jobs` | Celery app, schedule evaluation and runs |

## Data flow

1. A connector returns normalised records (`SyncBatch`). The importer upserts issues, PRs, reviews, checks,
   identities and **evidence rows** (one per issue/PR/comment/message, with URL and provider timestamp).
2. The risk scan runs after sync and upserts findings keyed by `dedupe_key`; findings whose condition has cleared
   are auto-resolved, human dismissals are preserved.
3. A question enters the coordinator, which picks an intent from the *user's text only*. The evidence collector
   calls typed tools; every tool result adds evidence ids to the workflow's *retrieved set*. The report writer builds
   a template answer; citations not in the retrieved set are stripped (`[unverified]`). If an LLM is configured it
   rewrites the draft, and any output with unknown citations is discarded in favour of the template.
4. The action planner may create a *proposal* (pending approval). Execution is a separate, human-triggered endpoint.

## Memory and provenance

Authoritative provider data (issues, PRs, evidence) is separate from conversational context (conversations/messages),
AI-generated conclusions (summary snapshots, findings with method labels) and user-supplied context (`memory_items`
with provenance). Changing project facts are always re-read from synchronized data, never from remembered text.
No vector database; evidence search uses PostgreSQL full-text search with a substring fallback.

## Known gaps

Live connectors, OAuth/PKCE, webhooks, calendar tools, a stuck-`executing` sweeper, and Redis-backed rate limiting.
