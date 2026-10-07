# Integrations

## Status

| Provider | Stage | State in this build |
| --- | --- | --- |
| Jira | 2 | **Demo connector only.** Live connector not implemented. |
| GitHub | 2 | **Demo connector only.** Live connector not implemented. |
| Slack | 3 | **Demo connector only** (reads fixture messages, simulates posting). |
| Google Calendar | 3 | Not implemented. `get_calendar_availability` / `propose_calendar_event` return “not implemented”. |
| Notion | 4 | Not implemented. |

Nothing here has been verified against a live provider.

## Connector contract (`app/integrations/base.py`)

Implement `Connector`:

- `validate_connection()` → ok, scopes, capabilities (capabilities gate which actions may execute)
- `discover_resources()`
- `sync(mode, checkpoint)` → `SyncBatch(records, next_checkpoint, partial_errors)` for `initial` / `incremental`
- `execute_action(action_type, target, payload, idempotency_key)`
- `find_existing_action(idempotency_key)` – used to reconcile uncertain outcomes
- raise `ProviderError(kind, message, retry_after)` with kind in `auth | permission | rate_limit | timeout |
  not_found | transient | invalid | unknown`

Register the class in `app/services/sync.py::get_connector`. Records use the normalised shape produced by
`app/integrations/demo.py::fixture` (sprints, identities, issues, pull_requests with reviews/checks, comments).
The importer handles dedup, evidence creation and identity handling, so a connector only maps provider JSON to
this shape.

## What a real connector still needs (per spec)
Pagination, request timeouts, `Retry-After` handling and bounded backoff, expired/revoked credential handling,
partial-failure reporting (`partial_errors`), minimum scopes, OAuth state + PKCE where supported, webhook
signature verification and delivery de-duplication, and provider-idempotent writes (Jira comments have no native
idempotency key, so reconcile by searching for the marker comment before retrying). Test these with a mocked
transport (`httpx.MockTransport`) and recorded fixtures; never against real workspaces from automated tests.

## Local-token limitations
Personal API tokens (GitHub PAT, Jira API token, Slack bot token) are acceptable for local development only: they
carry the creating user's permissions, do not expire automatically, and bypass consent screens. Prefer OAuth apps
for anything shared.
