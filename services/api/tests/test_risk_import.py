from datetime import timedelta

from sqlalchemy import func, select

from app.core.time import utcnow
from app.integrations.demo import DemoConnector
from app.models import (Evidence, ExternalIdentity, IntegrationConnection, Issue, Project, PullRequest, RiskFinding,
                        SyncCheckpoint, User)
from app.services.importer import import_batch
from app.services.risk import scan_project
from app.services.sync import run_sync
from tests.conftest import login_as


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def findings(db, **kw):
    return list(db.scalars(select(RiskFinding).filter_by(**kw)))


def test_seed_produces_expected_findings(db, seeded):
    by_method = {}
    for f in findings(db):
        by_method.setdefault(f.method, []).append(f)
    assert {"explicit_blocker", "overdue_issue", "stale_in_progress", "pr_awaiting_review", "failed_checks",
            "unresolved_dependency", "sprint_end_pending_work", "slack_blocker_signal"} <= set(by_method)
    blocker = by_method["explicit_blocker"][0]
    assert blocker.confidence == "confirmed" and blocker.severity == "high" and blocker.evidence_ids
    assert all(f.confidence == "possible" for f in by_method["stale_in_progress"])  # inactivity never proves a block
    assert by_method["slack_blocker_signal"][0].confidence == "possible"
    assert all(f.evidence_ids for f in findings(db))  # every finding cites evidence


def test_injection_text_is_not_promoted_to_a_finding(db, seeded):
    inj = db.scalar(select(Evidence).where(Evidence.external_id == "c-105-x"))
    assert inj is not None  # kept as untrusted evidence
    assert not [f for f in findings(db) if inj.id in f.evidence_ids]


def test_repeated_scans_do_not_duplicate(db, seeded):
    project = db.scalar(select(Project))
    before = count(db, RiskFinding)
    for _ in range(3):
        scan_project(db, project)
    db.commit()
    assert count(db, RiskFinding) == before


def test_threshold_boundaries(db, seeded):
    project = db.scalar(select(Project))
    now = utcnow()
    issue = db.scalar(select(Issue).where(Issue.key == "ATLAS-104"))
    issue.last_activity_at = now - timedelta(days=3)  # exactly at the 3-day threshold: NOT stale
    db.flush()
    scan_project(db, project, now=now)
    assert not [f for f in findings(db, issue_id=issue.id, method="stale_in_progress") if f.status == "open"]
    issue.last_activity_at = now - timedelta(days=3, seconds=1)  # just past it
    db.flush()
    scan_project(db, project, now=now)
    assert [f for f in findings(db, issue_id=issue.id, method="stale_in_progress") if f.status == "open"]
    pr = db.scalar(select(PullRequest).where(PullRequest.number == 218))
    pr.review_requested_at = now - timedelta(hours=48)
    db.flush()
    scan_project(db, project, now=now)
    assert findings(db, pull_request_id=pr.id, method="pr_awaiting_review")[0].status == "resolved"
    pr.review_requested_at = now - timedelta(hours=48, seconds=1)
    db.flush()
    scan_project(db, project, now=now)
    assert findings(db, pull_request_id=pr.id, method="pr_awaiting_review")[0].status == "open"


def test_editable_thresholds_change_results(client, seeded, db):
    admin = login_as(client, "admin")
    assert admin.req("PATCH", "/settings", json={"risk_thresholds": {"stale_days": 30}}).status_code == 200
    mgr = login_as(client, "manager")
    assert mgr.post("/projects/1/risks/scan").status_code == 200
    stale = [r for r in mgr.get("/projects/1/risks").json() if r["method"] == "stale_in_progress"]
    assert all(r["status"] == "resolved" for r in stale)


def test_condition_cleared_auto_resolves_but_dismissed_stays(db, seeded):
    project = db.scalar(select(Project))
    dep = db.scalar(select(Issue).where(Issue.key == "ATLAS-107"))
    stale = findings(db, issue_id=dep.id, method="stale_in_progress")[0]
    stale.status, stale.status_updated_by = "dismissed", db.scalar(select(User.id))
    dep.status, dep.status_category = "Done", "done"
    db.flush()
    scan_project(db, project)
    db.refresh(stale)
    assert stale.status == "dismissed"  # human decision preserved
    blocked_id = db.scalar(select(Issue.id).where(Issue.key == "ATLAS-101"))
    assert findings(db, issue_id=blocked_id, method="unresolved_dependency")[0].status == "resolved"


def test_import_is_idempotent_and_deduplicated(db, seeded):
    project = db.scalar(select(Project))
    counts = {m: count(db, m) for m in (Issue, PullRequest, Evidence, ExternalIdentity)}
    for provider in ("jira", "github", "slack"):
        batch = DemoConnector(provider, db, project.workspace_id).sync("initial", None)
        stats = import_batch(db, project, provider, batch)
        assert not any(k.endswith("_created") for k in stats), stats
    assert {m: count(db, m) for m in counts} == counts


def test_incremental_sync_uses_checkpoint(db, seeded):
    conn = db.scalar(select(IntegrationConnection).where(IntegrationConnection.provider == "jira"))
    project = db.scalar(select(Project))
    assert db.scalar(select(SyncCheckpoint).where(SyncCheckpoint.connection_id == conn.id)).cursor
    run = run_sync(db, conn, project)
    assert run.mode == "incremental" and run.status == "succeeded"


def test_identities_never_linked_by_display_name(db, seeded):
    ids = list(db.scalars(select(ExternalIdentity)))
    assert ids and all(i.user_id is None and i.status == "unresolved" for i in ids)


def test_identity_confirmation_requires_manager_and_membership(client, seeded):
    member = login_as(client, "member")
    iid = member.get("/identities").json()[0]["id"]
    assert member.req("PUT", f"/identities/{iid}", json={"user_id": member.me["user"]["id"]}).status_code == 403
    mgr = login_as(client, "manager")
    assert mgr.req("PUT", f"/identities/{iid}", json={"user_id": mgr.me["user"]["id"]}).json()["status"] == "confirmed"
    assert mgr.req("PUT", f"/identities/{iid}", json={"user_id": 99999}).status_code == 422
