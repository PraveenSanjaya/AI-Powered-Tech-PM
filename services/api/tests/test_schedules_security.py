import logging
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.crypto import CryptoError, decrypt_json, encrypt_json
from app.core.logging import JsonFormatter, redact
from app.jobs.schedules import latest_slot, run_schedule, tick
from app.models import (ActionProposal, AuditEvent, AutomationRun, AutomationSchedule, IntegrationConnection,
                        SimulatedExternalAction, SummarySnapshot)
from app.services.audit import audit
from tests.conftest import login_as


def test_schedule_slot_respects_timezone():
    sch = AutomationSchedule(kind="daily_summary", timezone="Asia/Colombo", time_of_day="09:00")
    # 09:00 Asia/Colombo (UTC+5:30) == 03:30 UTC
    now = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)
    assert latest_slot(sch, now) == datetime(2026, 10, 7, 3, 30, tzinfo=timezone.utc)
    before = datetime(2026, 10, 7, 3, 29, tzinfo=timezone.utc)
    assert latest_slot(sch, before) == datetime(2026, 10, 6, 3, 30, tzinfo=timezone.utc)
    weekly = AutomationSchedule(kind="weekly_review", timezone="Asia/Colombo", time_of_day="09:00", weekday=0)
    assert latest_slot(weekly, now).astimezone(timezone.utc).weekday() == 0  # Monday 2026-10-05


def test_tick_runs_due_schedule_once_only(db, seeded):
    sch = db.scalar(select(AutomationSchedule))
    sch.enabled = True
    db.commit()
    now = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)
    assert len(tick(db, now)) == 1
    assert tick(db, now) == []  # same slot: deduplicated
    assert db.scalar(select(func.count()).select_from(AutomationRun)) == 1
    assert db.scalar(select(func.count()).select_from(SummarySnapshot)) == 1
    # direct duplicate claim of the same slot is rejected by the database constraint
    slot = latest_slot(sch, now)
    assert run_schedule(db, sch, slot) is None


def test_schedule_without_policy_only_drafts(db, seeded):
    sch = db.scalar(select(AutomationSchedule))
    sch.destination = {"channel": "#atlas-dev"}
    sch.enabled = True
    db.commit()
    tick(db, datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc))
    p = db.scalar(select(ActionProposal))
    assert p.status == "pending_approval" and p.proposed_by_system == "automation"
    assert db.scalar(select(func.count()).select_from(SimulatedExternalAction)) == 0


def test_explicit_policy_allows_scoped_posting(client, seeded, db):
    admin = login_as(client, "admin")
    mgr_id = login_as(client, "manager").me["user"]["id"]
    admin = login_as(client, "admin")
    body = {"project_id": 1, "kind": "daily_summary", "timezone": "Asia/Colombo", "time_of_day": "09:00",
            "destination": {"channel": "#atlas-dev"}, "enabled": True,
            "policy": {"allow_external_post": True, "action_type": "slack_message", "destination": "#atlas-dev",
                       "owner_user_id": mgr_id}}
    assert admin.post("/automation/schedules", json=body).status_code == 201
    mgr = login_as(client, "manager")
    assert mgr.post("/automation/schedules", json=body).status_code == 403  # managers cannot grant external posting
    admin = login_as(client, "admin")
    bad = {**body, "policy": {**body["policy"], "destination": "#other"}}
    assert admin.post("/automation/schedules", json=bad).status_code == 422
    badtz = {**body, "timezone": "Nowhere/City", "policy": {}}
    assert admin.post("/automation/schedules", json=badtz).status_code == 422
    tick(db, datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc))
    posted = db.scalar(select(SimulatedExternalAction).where(SimulatedExternalAction.action_type == "slack_message"))
    assert posted is not None and posted.target == {"channel": "#atlas-dev"}
    assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "action.approved_by_policy"))


def test_schedule_failure_is_recorded_visibly(client, seeded, db, monkeypatch):
    sch = db.scalar(select(AutomationSchedule))
    monkeypatch.setattr("app.jobs.schedules.scan_project", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    run = run_schedule(db, sch, datetime(2026, 10, 7, 3, 30, tzinfo=timezone.utc))
    assert run.status == "failed" and "boom" in run.error
    mgr = login_as(client, "manager")
    assert mgr.get("/automation/runs").json()[0]["status"] == "failed"


# ---- secrets ------------------------------------------------------------------------------------------------------
def test_credentials_encrypt_roundtrip_and_wrong_key_fails(monkeypatch):
    token = encrypt_json({"token": "ghp_supersecret"})
    assert "ghp_supersecret" not in token
    assert decrypt_json(token) == {"token": "ghp_supersecret"}
    from cryptography.fernet import Fernet
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "credential_encryption_key", Fernet.generate_key().decode())
    try:
        decrypt_json(token)
        assert False
    except CryptoError:
        pass


def test_logs_and_audit_redact_secrets():
    assert "ghp_abcdef" not in redact("calling with token=ghp_abcdef123 now")
    assert "hunter2" not in redact('{"password": "hunter2"}')
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "Authorization: Bearer sk-ant-abc123", None, None)
    assert "sk-ant-abc123" not in JsonFormatter().format(rec)


def test_audit_payload_scrubs_sensitive_keys(db, seeded):
    ev = audit(db, 1, "test", payload={"token": "abc", "nested": {"password": "p", "ok": "fine"}, "note": "api_key=zzz"})
    assert ev.payload["token"] == "[REDACTED]" and ev.payload["nested"]["password"] == "[REDACTED]"
    assert ev.payload["nested"]["ok"] == "fine" and "zzz" not in ev.payload["note"]


def test_integration_api_never_returns_credentials(client, seeded, db):
    conn = db.scalar(select(IntegrationConnection).where(IntegrationConnection.provider == "jira"))
    conn.encrypted_credentials = encrypt_json({"token": "ghp_supersecret"})
    db.commit()
    admin = login_as(client, "admin")
    text = admin.get("/integrations").text
    assert "ghp_supersecret" not in text and "encrypted_credentials" not in text and "credentials" not in text.replace(
        "stored credentials", "")


def test_disconnect_clears_credentials_and_can_purge_data(client, seeded, db):
    from app.models import Issue
    conn = db.scalar(select(IntegrationConnection).where(IntegrationConnection.provider == "jira"))
    conn.encrypted_credentials = encrypt_json({"token": "t"})
    db.commit()
    admin = login_as(client, "admin")
    assert admin.req("DELETE", f"/integrations/{conn.id}?purge_data=true").status_code == 204
    db.expire_all()
    c = db.get(IntegrationConnection, conn.id)
    assert c.status == "disconnected" and c.encrypted_credentials is None
    assert db.scalar(select(func.count()).select_from(Issue)) == 0
    mgr = login_as(client, "manager")
    assert mgr.req("DELETE", f"/integrations/{conn.id}").status_code == 403  # admin only


def test_health_endpoints(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["database"] is True
