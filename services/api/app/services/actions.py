"""Action proposal state machine.

draft -> pending_approval -> approved -> executing -> succeeded
                         \\-> rejected | expired | failed | unknown_outcome

Approval binds to an immutable payload version + hash. Editing creates a new version and withdraws approval.
Only an authenticated human (via the API) may approve; no code path in the assistant can call `approve`.
"""
import hashlib
import json
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utcnow
from app.integrations.base import ProviderError
from app.models import (ActionExecution, ActionPayloadVersion, ActionProposal, IntegrationConnection, Membership,
                        Project)
from app.services.audit import audit
from app.services.sync import get_connector

ACTION_PROVIDER = {"jira_comment": "jira", "slack_message": "slack", "calendar_event": "google_calendar"}
ROLE_RANK = {"member": 1, "manager": 2, "admin": 3}


class ActionError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def payload_hash(payload: dict, target: dict) -> str:
    return hashlib.sha256(json.dumps({"p": payload, "t": target}, sort_keys=True).encode()).hexdigest()


def validate(action_type: str, target: dict, payload: dict) -> None:
    if action_type not in ACTION_PROVIDER:
        raise ActionError(f"Unsupported action type: {action_type}", 422)
    if action_type == "jira_comment":
        if not target.get("issue_key"):
            raise ActionError("target.issue_key is required", 422)
        body = (payload.get("body") or "").strip()
        if not body or len(body) > 8000:
            raise ActionError("payload.body must be 1-8000 characters", 422)
    elif action_type == "slack_message":
        if not target.get("channel") or not (payload.get("text") or "").strip():
            raise ActionError("target.channel and payload.text are required", 422)
    elif action_type == "calendar_event":
        tz = payload.get("timezone")
        try:
            ZoneInfo(tz or "")
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise ActionError("payload.timezone must be a valid IANA timezone, e.g. Asia/Colombo", 422)
        for k in ("title", "start", "end", "attendees"):
            if not payload.get(k):
                raise ActionError(f"payload.{k} is required", 422)


def create_proposal(db: Session, *, project: Project, user_id: int | None, action_type: str, target: dict,
                    payload: dict, reason: str, evidence_ids: list[int], workflow_run_id: int | None = None,
                    system: str = "action_planner") -> ActionProposal:
    validate(action_type, target, payload)
    provider = ACTION_PROVIDER[action_type]
    conn = db.scalar(select(IntegrationConnection).where(
        IntegrationConnection.workspace_id == project.workspace_id, IntegrationConnection.provider == provider,
        IntegrationConnection.status == "connected"))
    p = ActionProposal(workspace_id=project.workspace_id, project_id=project.id, requested_by=user_id,
                       proposed_by_system=system, provider=provider, action_type=action_type, target=target,
                       reason=reason, evidence_ids=evidence_ids, status="pending_approval", current_version=1,
                       expires_at=utcnow() + timedelta(hours=get_settings().action_expiry_hours),
                       is_demo=bool(conn and conn.is_demo), idempotency_key=uuid.uuid4().hex,
                       workflow_run_id=workflow_run_id)
    db.add(p)
    db.flush()
    db.add(ActionPayloadVersion(proposal_id=p.id, version=1, payload=payload,
                                payload_hash=payload_hash(payload, target), created_by=user_id))
    audit(db, p.workspace_id, "action.proposed", actor_user_id=user_id,
          actor_type="assistant" if system != "user" else "user", target_type="action_proposal", target_id=p.id,
          payload={"action_type": action_type, "target": target})
    db.flush()
    return p


def latest_version(db: Session, p: ActionProposal) -> ActionPayloadVersion:
    return db.scalar(select(ActionPayloadVersion).where(
        ActionPayloadVersion.proposal_id == p.id, ActionPayloadVersion.version == p.current_version))


def _role(db: Session, workspace_id: int, user_id: int) -> str | None:
    m = db.scalar(select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user_id))
    return m.role if m else None


def _require_manager(db: Session, p: ActionProposal, user_id: int) -> None:
    role = _role(db, p.workspace_id, user_id)
    if role is None or ROLE_RANK[role] < ROLE_RANK["manager"]:
        raise ActionError("Manager role required", 403)


def expire_if_needed(db: Session, p: ActionProposal) -> None:
    if p.status in ("pending_approval", "approved", "draft") and p.expires_at < utcnow():
        p.status = "expired"
        audit(db, p.workspace_id, "action.expired", actor_type="system", target_type="action_proposal", target_id=p.id)
        db.flush()


def edit_payload(db: Session, p: ActionProposal, user_id: int, payload: dict, target: dict | None = None) -> ActionProposal:
    _require_manager(db, p, user_id)
    expire_if_needed(db, p)
    if p.status not in ("pending_approval", "approved", "draft"):
        raise ActionError(f"Cannot edit a proposal in status '{p.status}'")
    target = target or p.target
    validate(p.action_type, target, payload)
    p.current_version += 1
    p.target = target
    db.add(ActionPayloadVersion(proposal_id=p.id, version=p.current_version, payload=payload,
                                payload_hash=payload_hash(payload, target), created_by=user_id))
    was_approved = p.status == "approved"
    p.status = "pending_approval"  # approval of an older version never carries over
    p.approved_version = p.approved_payload_hash = p.approved_by = p.approved_at = None
    audit(db, p.workspace_id, "action.edited", actor_user_id=user_id, target_type="action_proposal", target_id=p.id,
          payload={"version": p.current_version, "approval_invalidated": was_approved})
    db.flush()
    return p


def approve(db: Session, p: ActionProposal, user_id: int, version: int) -> ActionProposal:
    _require_manager(db, p, user_id)
    expire_if_needed(db, p)
    if p.status != "pending_approval":
        raise ActionError(f"Cannot approve a proposal in status '{p.status}'")
    if version != p.current_version:
        raise ActionError("Payload changed since you reviewed it; review the latest version", 409)
    v = latest_version(db, p)
    p.status, p.approved_version, p.approved_payload_hash = "approved", v.version, v.payload_hash
    p.approved_by, p.approved_at = user_id, utcnow()
    audit(db, p.workspace_id, "action.approved", actor_user_id=user_id, target_type="action_proposal", target_id=p.id,
          payload={"version": v.version, "payload_hash": v.payload_hash})
    db.flush()
    return p


def reject(db: Session, p: ActionProposal, user_id: int, note: str = "") -> ActionProposal:
    _require_manager(db, p, user_id)
    if p.status not in ("pending_approval", "approved", "draft"):
        raise ActionError(f"Cannot reject a proposal in status '{p.status}'")
    p.status = "rejected"
    audit(db, p.workspace_id, "action.rejected", actor_user_id=user_id, target_type="action_proposal",
          target_id=p.id, payload={"note": note[:500]})
    db.flush()
    return p


def execute(db: Session, proposal_id: int, user_id: int) -> ActionProposal:
    """Execute an approved proposal. Safe to call repeatedly: a succeeded proposal is returned untouched, and an
    uncertain outcome is reconciled against the provider before any retry."""
    p = db.scalar(select(ActionProposal).where(ActionProposal.id == proposal_id).with_for_update())
    if p is None:
        raise ActionError("Not found", 404)
    _require_manager(db, p, user_id)
    if p.status == "succeeded":
        return p  # idempotent replay
    expire_if_needed(db, p)
    if p.status == "executing":
        raise ActionError("Execution already in progress")
    if p.status not in ("approved", "failed", "unknown_outcome"):
        raise ActionError(f"Proposal is '{p.status}', not approved")
    v = latest_version(db, p)
    if p.approved_version != v.version or p.approved_payload_hash != v.payload_hash:
        raise ActionError("Approval does not match the current payload version")
    validate(p.action_type, p.target, v.payload)
    conn = db.scalar(select(IntegrationConnection).where(
        IntegrationConnection.workspace_id == p.workspace_id, IntegrationConnection.provider == p.provider,
        IntegrationConnection.status == "connected"))
    if conn is None:
        raise ActionError(f"No connected {p.provider} integration", 422)
    connector = get_connector(db, conn)
    if not connector.supports(p.action_type):
        raise ActionError(f"The connected {p.provider} account cannot perform {p.action_type}", 403)

    attempt = (db.scalar(select(ActionExecution.attempt).where(ActionExecution.proposal_id == p.id)
                         .order_by(ActionExecution.attempt.desc()).limit(1)) or 0) + 1
    if p.status == "unknown_outcome":  # reconcile before retrying; never blindly repost
        found = connector.find_existing_action(p.idempotency_key)
        if found:
            return _finish(db, p, attempt, v.version, "succeeded", found, user_id, reconciled=True)
    p.status = "executing"
    db.commit()  # make 'executing' visible so concurrent callers are refused
    p = db.scalar(select(ActionProposal).where(ActionProposal.id == proposal_id).with_for_update())
    try:
        res = connector.execute_action(p.action_type, p.target, v.payload, p.idempotency_key)
        return _finish(db, p, attempt, v.version, "succeeded", res, user_id)
    except ProviderError as exc:
        status = "unknown_outcome" if exc.kind == "timeout" else "failed"
        ex = ActionExecution(proposal_id=p.id, attempt=attempt, version=v.version,
                             status="unknown" if status == "unknown_outcome" else "failed", started_at=utcnow(),
                             finished_at=utcnow(), error=str(exc), result={"kind": exc.kind})
        db.add(ex)
        p.status = status
        audit(db, p.workspace_id, f"action.{status}", actor_user_id=user_id, target_type="action_proposal",
              target_id=p.id, payload={"attempt": attempt, "error_kind": exc.kind})
        db.flush()
        return p


def _finish(db, p, attempt, version, status, res, user_id, reconciled=False):
    db.add(ActionExecution(proposal_id=p.id, attempt=attempt, version=version, status=status, started_at=utcnow(),
                           finished_at=utcnow(), result={"external_id": res.external_id, "url": res.external_url,
                                                         "simulated": res.simulated, "reconciled": reconciled}))
    p.status, p.external_id, p.external_url = "succeeded", res.external_id, res.external_url
    audit(db, p.workspace_id, "action.executed", actor_user_id=user_id, target_type="action_proposal", target_id=p.id,
          payload={"attempt": attempt, "external_id": res.external_id, "simulated": res.simulated,
                   "reconciled": reconciled})
    db.flush()
    return p
