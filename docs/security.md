# Security

## Authentication and sessions
- Passwords hashed with Argon2id. Registration requires 10+ characters.
- Session token: 256-bit random, stored only as a SHA-256 hash; delivered in an `HttpOnly`, `SameSite=Lax` cookie
  (`Secure` when `COOKIE_SECURE=true`). Sessions expire after `SESSION_TTL_HOURS` (default 12).
- CSRF: every non-GET request authenticated by cookie must carry `X-CSRF-Token` equal to the per-session token
  returned by `/auth/me` or login. CORS is restricted to `CORS_ORIGINS` with an allow-list of headers/methods.
- Login and registration are rate-limited (in-process; use Redis for multi-instance deployments).

## Authorization and tenancy
- Roles: admin (settings, members, integrations, external-posting policies), manager (analyse, propose, approve,
  execute, scan, sync), member (read, read-only assistant).
- Every workspace route resolves membership first; non-members get 404. Every query filters by `workspace_id`
  (and project). Conversations are private to their owner. Tests cover guessed IDs across workspaces.
- Tools re-check role on each call. Draft/propose tools need manager.
- **Provider-side permissions:** execution re-checks the connection's declared capabilities. Live connectors must
  additionally surface the connected account's real scopes (not implemented yet; demo only).

## Prompt injection
Imported content (issue text, comments, chat messages) is untrusted data:
- Intents and tool selection come only from the authenticated user's message.
- There are no tools for shell, SQL, arbitrary HTTP, approval, execution or credentials.
- Drafts are built from structured fields; instruction-like imported text is withheld from answers and is never
  promoted to a finding by keyword matching.
- With an LLM, evidence goes inside `<untrusted>` tags with a system rule not to follow it, and output citing
  unknown evidence is rejected.
- The assistant cannot approve: approval requires an authenticated manager request carrying the reviewed version.
- Imported text is rendered as plain text; the Markdown renderer builds React elements and never creates links
  from text or uses `innerHTML`.

## Secrets
- Integration credentials are Fernet-encrypted with `CREDENTIAL_ENCRYPTION_KEY` from the environment.
- Never serialised to the API, audit payloads or logs (key-based audit scrubbing plus log redaction patterns).
- Disconnecting clears stored credentials; `purge_data=true` also deletes data imported from that provider.

## Reliability and hygiene
Request validation via Pydantic, restricted CORS, security headers, request ids, structured JSON logs, readiness
(`/readyz`) and liveness (`/healthz`), graceful uvicorn shutdown, Celery soft/hard time limits.

## Deleting data
- Conversation: `DELETE /conversations/{id}` (cascades messages).
- Integration data: `DELETE /integrations/{id}?purge_data=true` (admin).
- Whole workspace: delete the `workspaces` row; foreign keys cascade to all workspace-scoped tables.

## Not covered
No HTTPS termination, no WAF, no secrets manager integration, no dependency/vulnerability scanning pipeline, no
external audit. Do not expose this to the internet as-is.
