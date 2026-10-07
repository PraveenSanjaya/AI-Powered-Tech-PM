"""Scheduled workflows. Duplicate/overlap prevention comes from UNIQUE(schedule_id, scheduled_for): the run row is
inserted before any work, so a second worker (or a retry) for the same slot is rejected by the database.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import AutomationRun, AutomationSchedule, Membership, Project, SummarySnapshot
from app.services import actions as action_svc
from app.services import summary as S
from app.services.audit import audit
from app.services.risk import scan_project
from app.services.sync import run_sync  # noqa: F401  (re-exported for the periodic sync task)


def latest_slot(sch: AutomationSchedule, now: datetime) -> datetime | None:
    """Most recent scheduled instant (UTC) at or before `now`, honouring the schedule's own timezone."""
    tz = ZoneInfo(sch.timezone)
    local = now.astimezone(tz)
    hh, mm = map(int, sch.time_of_day.split(":"))
    for back in range(0, 8):
        day = (local - timedelta(days=back)).date()
        slot = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
        if slot > local:
            continue
        if sch.kind == "weekly_review" and slot.weekday() != (sch.weekday if sch.weekday is not None else 0):
            continue
        return slot.astimezone(timezone.utc)
    return None


def due_schedules(db: Session, now: datetime | None = None) -> list[tuple[AutomationSchedule, datetime]]:
    now = now or utcnow()
    out = []
    for sch in db.scalars(select(AutomationSchedule).where(AutomationSchedule.enabled.is_(True))):
        slot = latest_slot(sch, now)
        # only slots within the last 6 hours: do not replay history after downtime
        if slot and now - slot <= timedelta(hours=6) and not db.scalar(
                select(AutomationRun.id).where(AutomationRun.schedule_id == sch.id, AutomationRun.scheduled_for == slot)):
            out.append((sch, slot))
    return out


def run_schedule(db: Session, sch: AutomationSchedule, scheduled_for: datetime) -> AutomationRun | None:
    run = AutomationRun(schedule_id=sch.id, workspace_id=sch.workspace_id, scheduled_for=scheduled_for,
                        status="running", started_at=utcnow(), result={})
    db.add(run)
    try:
        db.commit()  # claim the slot durably before doing any work
    except IntegrityError:  # another worker already owns this slot
        db.rollback()
        return None
    try:
        project = db.get(Project, sch.project_id)
        result: dict = {}
        if sch.kind in ("blocker_scan", "weekly_review", "daily_summary"):
            result["scan"] = scan_project(db, project)
        if sch.kind in ("daily_summary", "weekly_review"):
            sprint = S.active_sprint(db, project)
            f = S.collect(db, project, sprint)
            _, secs = S.build_sections(f, sprint)
            md = S.render_markdown(secs, f["fresh"], f"{sch.kind.replace('_', ' ').title()} — {project.name}",
                                   f["fresh"].get("is_demo", False))
            snap = SummarySnapshot(workspace_id=sch.workspace_id, project_id=project.id,
                                   sprint_id=sprint.id if sprint else None, content={"schedule_id": sch.id},
                                   markdown=md, evidence_ids=S.evidence_ids_in(secs),
                                   generated_by="schedule:template")
            db.add(snap)
            db.flush()
            result["summary_snapshot_id"] = snap.id
            dest = sch.destination or {}
            if dest.get("channel"):
                result["proposal_id"] = _draft_post(db, sch, project, md, snap.evidence_ids, dest)
        run.status, run.result = "succeeded", result
    except Exception as exc:  # noqa: BLE001 - recorded for visibility, never swallowed silently
        db.rollback()
        run = db.scalar(select(AutomationRun).where(AutomationRun.schedule_id == sch.id,
                                                    AutomationRun.scheduled_for == scheduled_for)) or run
        run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
    run.finished_at = utcnow()
    audit(db, sch.workspace_id, "automation.run", actor_type="system", target_type="automation_schedule",
          target_id=sch.id, payload={"status": run.status, "scheduled_for": scheduled_for.isoformat()})
    db.commit()
    return run


def _draft_post(db, sch, project, markdown, evidence_ids, dest) -> int:
    """Default: a draft awaiting human approval. Only an explicit policy lets the owner's approval be pre-authorised."""
    p = action_svc.create_proposal(db, project=project, user_id=sch.created_by, action_type="slack_message",
                                   target={"channel": dest["channel"]}, payload={"text": markdown[:3900]},
                                   reason=f"Scheduled {sch.kind}", evidence_ids=evidence_ids, system="automation")
    pol = sch.policy or {}
    owner = pol.get("owner_user_id")
    if (pol.get("allow_external_post") and pol.get("action_type") == "slack_message"
            and pol.get("destination") == dest["channel"] and owner
            and db.scalar(select(Membership).where(Membership.workspace_id == sch.workspace_id,
                                                   Membership.user_id == owner, Membership.role.in_(("manager", "admin"))))):
        action_svc.approve(db, p, owner, p.current_version)
        audit(db, sch.workspace_id, "action.approved_by_policy", actor_user_id=owner, actor_type="system",
              target_type="action_proposal", target_id=p.id, payload={"schedule_id": sch.id})
        action_svc.execute(db, p.id, owner)
    return p.id


def tick(db: Session, now: datetime | None = None) -> list[int]:
    ran = []
    for sch, slot in due_schedules(db, now):
        r = run_schedule(db, sch, slot)
        if r:
            ran.append(r.id)
    return ran
