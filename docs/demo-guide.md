# Demo guide

Everything below uses fictional data and simulated connectors.

1. **Start** the stack (`docker compose up --build`) and seed: `docker compose exec api python -m app.cli seed`.
2. **Log in** at <http://localhost:3000> as `manager@demo.example` / `demo-password-123`, choose the workspace and
   the ATLAS project. The purple DEMO MODE banner is always visible.
3. **Dashboard**: sprint goal and dates, issue counts, ATLAS-101 *confirmed blocked*, ATLAS-107 and ATLAS-104 as
   *possible* risks, overdue ATLAS-105, PR #218 awaiting review, PR #212 failing required `unit-tests`, and the
   freshness banner. Burndown shows an explicit empty state (no history is invented).
4. **Assistant** → ask *“What threatens our sprint goal?”*. The answer lists confirmed blockers, possible risks,
   PRs and next actions, each with `[EV-n]` links. Expand *Evidence* to see title, provider, timestamp and the
   (non-resolving, demo) source link. The label says it is a deterministic template response.
5. **Summary**: *Sprint detail* → *Generate summary* (or ask “Can you draft a sprint update?”).
6. **Prepare a Jira comment**: ask *“Prepare a Jira comment for ATLAS-101”*. Nothing is sent; a proposal is created.
7. **Inspect**: follow *Review proposed action*. The page shows the exact text, target (`ATLAS-101`), requester,
   reason, evidence, expiry and the demo notice. Try *Edit* → *Save as new version*: approval would be invalidated.
8. **Approve** (version N) then **Execute (simulated)**. The result shows an external id like `demo-jira-…` and a
   `demo.invalid` URL marked simulated.
9. **Audit**: *Audit history* lists `action.proposed`, `action.approved`, `action.executed`.
10. **Retry**: click *Retry (safe: no duplicate)*. The status stays `succeeded`, still one attempt, and the
    simulation ledger still contains one row.

Other things to try: switch to `member@demo.example` (cannot approve/execute, no audit access); ask “Draft a Slack
update for #atlas-dev”; open *Automation* and *Run now* on the disabled daily summary; as admin disconnect Jira on
*Integrations* and see the stale/disconnected warnings on the dashboard.
