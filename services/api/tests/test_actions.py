from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.time import utcnow
from app.integrations.base import ProviderError
from app.integrations.demo import DemoConnector
from app.main import app
from app.models import ActionExecution, ActionProposal, AuditEvent, SimulatedExternalAction
from app.services import actions as svc
from tests.conftest import login_as


def draft(sess):
    conv = sess.post("/conversations", json={"project_id": 1}).json()["id"]
    r = sess.post(f"/conversations/{conv}/messages", json={"text": "Prepare a Jira comment for ATLAS-101"}).json()
    return r["meta"]["proposal_id"]


def ledger(db):
    return db.scalar(select(func.count()).select_from(SimulatedExternalAction))


def test_full_demo_flow_and_idempotent_retry(manager, db):
    pid = draft(manager)
    a = manager.get(f"/actions/{pid}").json()
    assert a["status"] == "pending_approval" and a["target"] == {"issue_key": "ATLAS-101"}
    assert a["is_demo"] and "Nothing will be sent to a real service" in a["simulated_notice"]
    assert "ATLAS-101" in a["payload"]["body"] and a["requested_by"]["name"] == "Morgan Manager"
    assert a["citations"] and a["recipients"]
    assert manager.post(f"/actions/{pid}/execute").status_code == 409  # not approved yet
    assert manager.post(f"/actions/{pid}/approve", json={"version": a["version"]}).json()["status"] == "approved"
    done = manager.post(f"/actions/{pid}/execute").json()
    assert done["status"] == "succeeded" and done["external_id"].startswith("demo-jira-")
    assert done["external_url"].startswith("https://demo.invalid/")
    assert ledger(db) == 1
    again = manager.post(f"/actions/{pid}/execute").json()  # retry
    assert again["status"] == "succeeded" and again["external_id"] == done["external_id"]
    assert ledger(db) == 1 and db.scalar(select(func.count()).select_from(ActionExecution)) == 1
    events = [e for e in db.scalars(select(AuditEvent.event_type))]
    assert {"action.proposed", "action.approved", "action.executed"} <= set(events)
    audit = manager.get("/audit").json()
    assert any(e["event_type"] == "action.executed" and e["payload"]["simulated"] for e in audit)


def test_editing_invalidates_approval(manager, db):
    pid = draft(manager)
    manager.post(f"/actions/{pid}/approve", json={"version": 1})
    r = manager.req("PUT", f"/actions/{pid}/payload", json={"payload": {"body": "Edited text"}})
    assert r.json()["status"] == "pending_approval" and r.json()["version"] == 2
    assert manager.post(f"/actions/{pid}/execute").status_code == 409
    assert manager.post(f"/actions/{pid}/approve", json={"version": 1}).status_code == 409  # stale review
    assert manager.post(f"/actions/{pid}/approve", json={"version": 2}).status_code == 200
    out = manager.post(f"/actions/{pid}/execute").json()
    assert out["status"] == "succeeded"
    sim = db.scalar(select(SimulatedExternalAction))
    assert sim.payload == {"body": "Edited text"}  # exactly the approved version was executed
    assert len(manager.get(f"/actions/{pid}").json()["versions"]) == 2


def test_payload_tampering_after_approval_is_refused(manager, db):
    pid = draft(manager)
    manager.post(f"/actions/{pid}/approve", json={"version": 1})
    from app.models import ActionPayloadVersion
    v = db.scalar(select(ActionPayloadVersion).where(ActionPayloadVersion.proposal_id == pid))
    v.payload_hash = "0" * 64  # simulate out-of-band modification
    db.commit()
    assert manager.post(f"/actions/{pid}/execute").status_code == 409
    assert ledger(db) == 0


def test_member_cannot_approve_or_execute(client, seeded):
    mgr = login_as(client, "manager")
    pid = draft(mgr)
    member = login_as(client, "member")
    assert member.post(f"/actions/{pid}/approve", json={"version": 1}).status_code == 403
    assert member.post(f"/actions/{pid}/execute").status_code == 403
    assert member.req("PUT", f"/actions/{pid}/payload", json={"payload": {"body": "x"}}).status_code == 403
    assert member.get(f"/actions/{pid}").status_code == 200  # may view


def test_reject_expire_and_validation(manager, db):
    pid = draft(manager)
    assert manager.post(f"/actions/{pid}/reject", json={"note": "no"}).json()["status"] == "rejected"
    assert manager.post(f"/actions/{pid}/approve", json={"version": 1}).status_code == 409
    pid2 = draft(manager)
    p = db.get(ActionProposal, pid2)
    p.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert manager.post(f"/actions/{pid2}/approve", json={"version": 1}).status_code == 409
    assert manager.get(f"/actions/{pid2}").json()["status"] == "expired"
    bad = manager.post("/actions", json={"project_id": 1, "action_type": "jira_comment", "target": {},
                                         "payload": {"body": "x"}})
    assert bad.status_code == 422
    assert manager.post("/actions", json={"project_id": 1, "action_type": "delete_repo", "target": {},
                                          "payload": {}}).status_code == 422


def test_calendar_timezone_validation():
    ok = {"title": "Blocker sync", "start": "2026-10-08T10:00:00", "end": "2026-10-08T10:30:00",
          "attendees": ["a@x.example"], "timezone": "Asia/Colombo"}
    svc.validate("calendar_event", {}, ok)
    for tz in ("Mars/Olympus", "", None, "GMT+5:30"):
        with pytest.raises(svc.ActionError):
            svc.validate("calendar_event", {}, {**ok, "timezone": tz})


def test_calendar_event_cannot_execute_without_connector(manager):
    r = manager.post("/actions", json={"project_id": 1, "action_type": "calendar_event", "target": {}, "reason": "t",
                                       "payload": {"title": "Sync", "start": "2026-10-08T10:00:00",
                                                   "end": "2026-10-08T10:30:00", "attendees": ["a@x.example"],
                                                   "timezone": "Asia/Colombo"}})
    pid = r.json()["id"]
    manager.post(f"/actions/{pid}/approve", json={"version": 1})
    assert manager.post(f"/actions/{pid}/execute").status_code == 422  # no google_calendar connection


class _TimeoutAfterSuccess(DemoConnector):
    """Provider performed the action but the response was lost."""
    def execute_action(self, *a, **k):
        super().execute_action(*a, **k)
        raise ProviderError("timeout", "read timeout")


class _TimeoutBeforeSuccess(DemoConnector):
    def execute_action(self, *a, **k):
        raise ProviderError("timeout", "connect timeout")


def test_uncertain_outcome_is_reconciled_not_reposted(manager, db, monkeypatch):
    pid = draft(manager)
    manager.post(f"/actions/{pid}/approve", json={"version": 1})
    monkeypatch.setattr("app.services.actions.get_connector",
                        lambda db_, conn: _TimeoutAfterSuccess(conn.provider, db_, conn.workspace_id))
    r = manager.post(f"/actions/{pid}/execute").json()
    assert r["status"] == "unknown_outcome"
    assert ledger(db) == 1  # provider did perform it
    monkeypatch.undo()
    r = manager.post(f"/actions/{pid}/execute").json()  # retry: must reconcile instead of re-posting
    assert r["status"] == "succeeded" and ledger(db) == 1
    assert [e["result"].get("reconciled") for e in manager.get(f"/actions/{pid}").json()["executions"]][-1] is True


def test_uncertain_outcome_that_never_happened_is_retried_once(manager, db, monkeypatch):
    pid = draft(manager)
    manager.post(f"/actions/{pid}/approve", json={"version": 1})
    monkeypatch.setattr("app.services.actions.get_connector",
                        lambda db_, conn: _TimeoutBeforeSuccess(conn.provider, db_, conn.workspace_id))
    assert manager.post(f"/actions/{pid}/execute").json()["status"] == "unknown_outcome"
    assert ledger(db) == 0
    monkeypatch.undo()
    assert manager.post(f"/actions/{pid}/execute").json()["status"] == "succeeded"
    assert ledger(db) == 1


def test_definitive_failure_marks_failed_and_is_retryable(manager, db, monkeypatch):
    pid = draft(manager)
    manager.post(f"/actions/{pid}/approve", json={"version": 1})

    class Denied(DemoConnector):
        def execute_action(self, *a, **k):
            raise ProviderError("permission", "token lacks scope")
    monkeypatch.setattr("app.services.actions.get_connector", lambda d, c: Denied(c.provider, d, c.workspace_id))
    assert manager.post(f"/actions/{pid}/execute").json()["status"] == "failed"
    monkeypatch.undo()
    assert manager.post(f"/actions/{pid}/execute").json()["status"] == "succeeded"
    assert ledger(db) == 1


def test_integration_capability_is_rechecked_before_execution(manager, db):
    pid = draft(manager)
    manager.post(f"/actions/{pid}/approve", json={"version": 1})
    from app.models import IntegrationConnection
    c = db.scalar(select(IntegrationConnection).where(IntegrationConnection.provider == "jira"))
    c.status = "disconnected"
    db.commit()
    assert manager.post(f"/actions/{pid}/execute").status_code == 422
    assert ledger(db) == 0


def test_action_belongs_to_workspace(seeded):
    with TestClient(app) as a, TestClient(app) as b:
        mgr = login_as(a, "manager")
        pid = draft(mgr)
        r = b.post("/api/auth/register", json={"email": "x@other.example", "name": "X", "password": "a-long-password-1",
                                               "workspace_name": "Other"}).json()
        h = {"x-csrf-token": r["csrf_token"]}
        ows = r["workspaces"][0]["id"]
        for verb in ("approve", "execute", "reject"):
            resp = b.post(f"/api/workspaces/{ows}/actions/{pid}/{verb}", json={"version": 1}, headers=h)
            assert resp.status_code == 404
