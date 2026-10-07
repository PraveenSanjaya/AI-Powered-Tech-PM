from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.workspaces import get_project
from app.auth.deps import Ctx, ws_admin, ws_manager, ws_member
from app.core.config import get_settings
from app.jobs.schedules import run_schedule
from app.models import (AuditEvent, AutomationRun, AutomationSchedule, Evidence, IntegrationConnection, Issue,
                        Membership, Project, PullRequest, SyncRun)
from app.core.time import utcnow
from app.services.audit import audit
from app.services.seed import install_demo_connections
from app.services.sync import run_sync

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["integrations"])

CATALOG = [
    ("jira", "Jira", "Stage 2: live connector not implemented in this build"),
    ("github", "GitHub", "Stage 2: live connector not implemented in this build"),
    ("slack", "Slack", "Stage 3: live connector not implemented in this build"),
    ("google_calendar", "Google Calendar", "Stage 3: not implemented in this build"),
    ("notion", "Notion", "Stage 4: not implemented in this build"),
]


@router.get("/integrations")
def list_integrations(ctx: Ctx = Depends(ws_member)):
    conns = {c.provider: c for c in ctx.db.scalars(select(IntegrationConnection).where(
        IntegrationConnection.workspace_id == ctx.workspace_id))}
    out = []
    for key, label, note in CATALOG:
        c = conns.get(key)
        runs = []
        if c:
            runs = [{"id": r.id, "mode": r.mode, "status": r.status, "started_at": r.started_at.isoformat(),
                     "finished_at": r.finished_at.isoformat() if r.finished_at else None, "stats": r.stats,
                     "error": r.error} for r in ctx.db.scalars(select(SyncRun).where(SyncRun.connection_id == c.id)
                                                              .order_by(SyncRun.id.desc()).limit(5))]
        out.append({"provider": key, "label": label, "live_connector": False, "live_note": note,
                    "connection": None if c is None else {
                        "id": c.id, "status": c.status, "is_demo": c.is_demo, "capabilities": c.capabilities,
                        "scopes": c.scopes,
                        "last_success_at": c.last_success_at.isoformat() if c.last_success_at else None,
                        "last_error": c.last_error},  # never includes credentials
                    "recent_syncs": runs})
    return out


@router.post("/integrations/demo", status_code=201)
def connect_demo(ctx: Ctx = Depends(ws_admin)):
    if not get_settings().demo_mode:
        raise HTTPException(403, "Demo mode is disabled")
    install_demo_connections(ctx.db, ctx.workspace_id, ctx.user.id)
    audit(ctx.db, ctx.workspace_id, "integration.demo_connected", actor_user_id=ctx.user.id)
    ctx.db.commit()
    return {"ok": True}


@router.post("/integrations/{connection_id}/sync")
def sync(connection_id: int, ctx: Ctx = Depends(ws_manager)):
    c = ctx.db.scalar(select(IntegrationConnection).where(IntegrationConnection.id == connection_id,
                                                          IntegrationConnection.workspace_id == ctx.workspace_id))
    if c is None:
        raise HTTPException(404, "Connection not found")
    if c.status == "disconnected":
        raise HTTPException(409, "This connection is disconnected; reconnect it before syncing")
    project = ctx.db.scalar(select(Project).where(Project.workspace_id == ctx.workspace_id).order_by(Project.id))
    if project is None:
        raise HTTPException(409, "Create a project first")
    run = run_sync(ctx.db, c, project)
    audit(ctx.db, ctx.workspace_id, "integration.synced", actor_user_id=ctx.user.id, target_type="integration",
          target_id=c.id, payload={"status": run.status, "mode": run.mode})
    ctx.db.commit()
    return {"id": run.id, "status": run.status, "mode": run.mode, "stats": run.stats, "error": run.error}


@router.delete("/integrations/{connection_id}", status_code=204)
def disconnect(connection_id: int, purge_data: bool = False, ctx: Ctx = Depends(ws_admin)):
    """Disconnect: clears stored credentials. With purge_data=true also deletes data imported from that provider."""
    c = ctx.db.scalar(select(IntegrationConnection).where(IntegrationConnection.id == connection_id,
                                                          IntegrationConnection.workspace_id == ctx.workspace_id))
    if c is None:
        raise HTTPException(404, "Connection not found")
    c.status, c.encrypted_credentials = "disconnected", None
    if purge_data:
        for model in (Evidence, Issue, PullRequest):
            ctx.db.execute(delete(model).where(model.workspace_id == ctx.workspace_id, model.provider == c.provider))
    audit(ctx.db, ctx.workspace_id, "integration.disconnected", actor_user_id=ctx.user.id, target_type="integration",
          target_id=c.id, payload={"purge_data": purge_data})
    ctx.db.commit()


# ---- automation ---------------------------------------------------------------------------------------------------------
class ScheduleIn(BaseModel):
    project_id: int
    kind: str = Field(pattern="^(daily_summary|blocker_scan|weekly_review)$")
    timezone: str = Field(max_length=64)
    time_of_day: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekday: int | None = Field(None, ge=0, le=6)
    destination: dict = Field(default_factory=dict)
    enabled: bool = True
    policy: dict = Field(default_factory=dict)


def _sched_out(s: AutomationSchedule) -> dict:
    return {"id": s.id, "project_id": s.project_id, "kind": s.kind, "timezone": s.timezone,
            "time_of_day": s.time_of_day, "weekday": s.weekday, "destination": s.destination, "enabled": s.enabled,
            "policy": s.policy,
            "external_posting": bool((s.policy or {}).get("allow_external_post"))}


def _validate_schedule(ctx: Ctx, body: ScheduleIn) -> None:
    get_project(ctx, body.project_id)
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "timezone must be a valid IANA timezone, e.g. Asia/Colombo")
    if body.kind == "weekly_review" and body.weekday is None:
        raise HTTPException(422, "weekday is required for weekly_review")
    if body.destination and not str(body.destination.get("channel", "")).startswith("#"):
        raise HTTPException(422, "destination.channel must be a Slack channel such as #atlas-dev")
    pol = body.policy or {}
    if pol.get("allow_external_post"):
        if not ctx.can("admin"):
            raise HTTPException(403, "Only an admin can enable automatic external posting")
        owner, dest = pol.get("owner_user_id"), body.destination.get("channel")
        if pol.get("action_type") != "slack_message" or pol.get("destination") != dest or not dest or not owner:
            raise HTTPException(422, "Policy must name action_type=slack_message, the exact destination and an owner_user_id")
        if not ctx.db.scalar(select(Membership.id).where(Membership.workspace_id == ctx.workspace_id,
                                                         Membership.user_id == owner,
                                                         Membership.role.in_(("manager", "admin")))):
            raise HTTPException(422, "Policy owner must be a manager or admin of this workspace")


@router.get("/automation/schedules")
def list_schedules(ctx: Ctx = Depends(ws_member)):
    return [_sched_out(s) for s in ctx.db.scalars(select(AutomationSchedule).where(
        AutomationSchedule.workspace_id == ctx.workspace_id).order_by(AutomationSchedule.id))]


@router.post("/automation/schedules", status_code=201)
def create_schedule(body: ScheduleIn, ctx: Ctx = Depends(ws_manager)):
    _validate_schedule(ctx, body)
    s = AutomationSchedule(workspace_id=ctx.workspace_id, created_by=ctx.user.id, **body.model_dump())
    ctx.db.add(s)
    ctx.db.flush()
    audit(ctx.db, ctx.workspace_id, "automation.created", actor_user_id=ctx.user.id, target_type="automation_schedule",
          target_id=s.id, payload={"kind": s.kind, "external_posting": bool((s.policy or {}).get("allow_external_post"))})
    ctx.db.commit()
    return _sched_out(s)


def _sched(ctx: Ctx, sid: int) -> AutomationSchedule:
    s = ctx.db.scalar(select(AutomationSchedule).where(AutomationSchedule.id == sid,
                                                       AutomationSchedule.workspace_id == ctx.workspace_id))
    if s is None:
        raise HTTPException(404, "Schedule not found")
    return s


@router.put("/automation/schedules/{schedule_id}")
def update_schedule(schedule_id: int, body: ScheduleIn, ctx: Ctx = Depends(ws_manager)):
    s = _sched(ctx, schedule_id)
    _validate_schedule(ctx, body)
    if (s.policy or {}).get("allow_external_post") and not ctx.can("admin"):
        raise HTTPException(403, "Only an admin can modify a schedule that posts externally")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    audit(ctx.db, ctx.workspace_id, "automation.updated", actor_user_id=ctx.user.id, target_type="automation_schedule",
          target_id=s.id)
    ctx.db.commit()
    return _sched_out(s)


class EnabledIn(BaseModel):
    enabled: bool


@router.post("/automation/schedules/{schedule_id}/enabled")
def set_enabled(schedule_id: int, body: EnabledIn, ctx: Ctx = Depends(ws_manager)):
    s = _sched(ctx, schedule_id)
    if (s.policy or {}).get("allow_external_post") and not ctx.can("admin"):
        raise HTTPException(403, "Only an admin can change a schedule that posts externally")
    s.enabled = body.enabled
    audit(ctx.db, ctx.workspace_id, "automation.toggled", actor_user_id=ctx.user.id, target_type="automation_schedule",
          target_id=s.id, payload={"enabled": body.enabled})
    ctx.db.commit()
    return _sched_out(s)


@router.delete("/automation/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, ctx: Ctx = Depends(ws_manager)):
    ctx.db.delete(_sched(ctx, schedule_id))
    audit(ctx.db, ctx.workspace_id, "automation.deleted", actor_user_id=ctx.user.id, target_id=schedule_id)
    ctx.db.commit()


@router.post("/automation/schedules/{schedule_id}/run-now")
def run_now(schedule_id: int, ctx: Ctx = Depends(ws_manager)):
    s = _sched(ctx, schedule_id)
    run = run_schedule(ctx.db, s, utcnow())
    if run is None:
        raise HTTPException(409, "A run for this slot already exists")
    return {"id": run.id, "status": run.status, "result": run.result, "error": run.error}


@router.get("/automation/runs")
def list_runs(ctx: Ctx = Depends(ws_member)):
    rows = ctx.db.scalars(select(AutomationRun).where(AutomationRun.workspace_id == ctx.workspace_id)
                          .order_by(AutomationRun.id.desc()).limit(50))
    return [{"id": r.id, "schedule_id": r.schedule_id, "status": r.status, "scheduled_for": r.scheduled_for.isoformat(),
             "started_at": r.started_at.isoformat(), "result": r.result, "error": r.error} for r in rows]


# ---- audit ----------------------------------------------------------------------------------------------------------------
@router.get("/audit")
def audit_log(limit: int = 100, event_type: str | None = None, ctx: Ctx = Depends(ws_manager)):
    q = select(AuditEvent).where(AuditEvent.workspace_id == ctx.workspace_id)
    if event_type:
        q = q.where(AuditEvent.event_type == event_type)
    rows = ctx.db.scalars(q.order_by(AuditEvent.id.desc()).limit(min(limit, 500)))
    return [{"id": a.id, "event_type": a.event_type, "actor_type": a.actor_type, "actor_user_id": a.actor_user_id,
             "target_type": a.target_type, "target_id": a.target_id, "payload": a.payload,
             "created_at": a.created_at.isoformat()} for a in rows]
