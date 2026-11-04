from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.workspaces import get_project, sprint_out
from app.auth.deps import Ctx, ws_manager, ws_member
from app.core.time import utcnow
from app.models import (Evidence, ExternalIdentity, IntegrationConnection, Issue, PullRequest, PullRequestCheck,
                        RiskFinding)
from app.services import summary as S
from app.services.audit import audit
from app.services.risk import scan_project

router = APIRouter(prefix="/workspaces/{workspace_id}/projects/{project_id}", tags=["project-data"])


def issue_out(i: Issue, names: dict) -> dict:
    return {"id": i.id, "key": i.key, "title": i.title, "status": i.status, "status_category": i.status_category,
            "priority": i.priority, "assignee": names.get(i.assignee_identity_id), "assignee_id": i.assignee_identity_id,
            "is_blocked": i.is_blocked, "blocked_reason": i.blocked_reason, "story_points": i.story_points,
            "due_date": i.due_date.isoformat() if i.due_date else None, "url": i.url, "provider": i.provider,
            "sprint_id": i.sprint_id, "last_activity_at": i.last_activity_at.isoformat(),
            "last_synced_at": i.last_synced_at.isoformat(), "description": i.description}


def pr_out(p: PullRequest, names: dict) -> dict:
    return {"id": p.id, "number": p.number, "repo": p.repo, "title": p.title, "author": names.get(p.author_identity_id),
            "state": p.state, "draft": p.draft, "review_state": p.review_state, "checks_state": p.checks_state,
            "issue_key": p.issue_key, "url": p.url, "provider": p.provider,
            "review_requested_at": p.review_requested_at.isoformat() if p.review_requested_at else None,
            "updated_at": p.provider_updated_at.isoformat()}


def finding_out(r: RiskFinding, issues: dict, prs: dict) -> dict:
    i, p = issues.get(r.issue_id), prs.get(r.pull_request_id)
    return {"id": r.id, "type": r.type, "severity": r.severity, "confidence": r.confidence, "method": r.method,
            "explanation": r.explanation, "status": r.status, "evidence_ids": r.evidence_ids,
            "detected_at": r.detected_at.isoformat(), "last_seen_at": r.last_seen_at.isoformat(),
            "issue_key": i.key if i else None, "pr_number": p.number if p else None,
            "issue_title": i.title if i else None}


def _names(ctx: Ctx) -> dict:
    return {i.id: i.display_name for i in ctx.db.scalars(select(ExternalIdentity).where(
        ExternalIdentity.workspace_id == ctx.workspace_id))}


@router.get("/dashboard")
def dashboard(project_id: int, sprint_id: int | None = None, ctx: Ctx = Depends(ws_member)):
    project = get_project(ctx, project_id)
    sprint = S.active_sprint(ctx.db, project, sprint_id)
    if sprint_id and sprint is None:
        raise HTTPException(404, "Sprint not found")
    f = S.collect(ctx.db, project, sprint)
    names, issues, prs, today = f["names"], f["issues"], f["prs"], utcnow().date()
    by_status = Counter(i.status for i in issues)
    by_cat = Counter(i.status_category for i in issues)
    per_person = defaultdict(list)
    for i in issues:
        if i.status_category != "done":
            per_person[names.get(i.assignee_identity_id, "Unassigned")].append(i.key)
    conns = ctx.db.scalars(select(IntegrationConnection).where(IntegrationConnection.workspace_id == ctx.workspace_id))
    recent = ctx.db.scalars(select(Evidence).where(Evidence.project_id == project.id)
                            .order_by(Evidence.provider_timestamp.desc()).limit(8))
    return {
        "project": {"id": project.id, "key": project.key, "name": project.name},
        "sprint": sprint_out(sprint) if sprint else None,
        "counts": {"total": len(issues), "by_status": dict(by_status), "by_category": dict(by_cat)},
        "completed": by_cat.get("done", 0), "remaining": len(issues) - by_cat.get("done", 0),
        "blocked": [issue_out(i, names) for i in issues if i.is_blocked and i.status_category != "done"],
        "potentially_blocked": [
            {"finding_id": x.id, "explanation": x.explanation, "confidence": x.confidence, "issue_id": x.issue_id}
            for x in f["findings"] if x.type in ("stale_issue", "potential_blocker_signal", "unresolved_dependency")
            and x.confidence != "confirmed"],
        "overdue": [issue_out(i, names) for i in issues if i.due_date and i.due_date < today
                    and i.status_category != "done"],
        "prs_awaiting_review": [pr_out(p, names) for p in prs if not p.draft and p.review_state == "awaiting_review"],
        "failed_checks": [pr_out(p, names) for p in prs if p.checks_state == "failing"],
        "assigned_work": [{"name": n, "open_issues": v} for n, v in sorted(per_person.items())],
        "workload_note": "Open-issue counts are an incomplete indicator and not a measure of productivity.",
        "recent_changes": [{"id": e.id, "title": e.title, "kind": e.kind, "provider": e.provider, "url": e.url,
                            "timestamp": e.provider_timestamp.isoformat(),
                            "is_demo": e.url.startswith("https://demo.invalid")} for e in recent],
        "freshness": f["fresh"],
        "integrations": [{"provider": c.provider, "status": c.status, "is_demo": c.is_demo,
                          "last_success_at": c.last_success_at.isoformat() if c.last_success_at else None,
                          "last_error": c.last_error} for c in conns],
        # No fabricated history: snapshots of past states are not collected yet.
        "burndown": {"available": False, "reason": "Historical sprint snapshots have not been collected yet."},
        "open_findings": len(f["findings"]),
    }


@router.get("/issues")
def list_issues(project_id: int, sprint_id: int | None = None, status: str | None = None,
                assignee_id: int | None = None, unassigned: bool = False, q: str | None = Query(None, max_length=100),
                limit: int = Query(100, le=200), offset: int = 0, ctx: Ctx = Depends(ws_member)):
    get_project(ctx, project_id)
    qy = select(Issue).where(Issue.project_id == project_id, Issue.workspace_id == ctx.workspace_id)
    if sprint_id:
        qy = qy.where(Issue.sprint_id == sprint_id)
    if status:
        qy = qy.where(Issue.status == status)
    if assignee_id:
        qy = qy.where(Issue.assignee_identity_id == assignee_id)
    if unassigned:
        qy = qy.where(Issue.assignee_identity_id.is_(None))
    if q:
        qy = qy.where(Issue.title.ilike(f"%{q}%") | Issue.key.ilike(f"%{q}%"))
    total = ctx.db.scalar(select(func.count()).select_from(qy.subquery()))
    rows = ctx.db.scalars(qy.order_by(Issue.key).limit(limit).offset(offset))
    names = _names(ctx)
    return {"total": total, "items": [issue_out(i, names) for i in rows]}


@router.get("/issues/{issue_id}")
def issue_detail(project_id: int, issue_id: int, ctx: Ctx = Depends(ws_member)):
    get_project(ctx, project_id)
    i = ctx.db.scalar(select(Issue).where(Issue.id == issue_id, Issue.project_id == project_id,
                                          Issue.workspace_id == ctx.workspace_id))
    if i is None:
        raise HTTPException(404, "Issue not found")
    ev = ctx.db.scalars(select(Evidence).where(Evidence.project_id == project_id, Evidence.ref_key == i.key)
                        .order_by(Evidence.provider_timestamp.desc()))
    findings = ctx.db.scalars(select(RiskFinding).where(RiskFinding.issue_id == i.id))
    return {**issue_out(i, _names(ctx)),
            "evidence": [{"id": e.id, "title": e.title, "kind": e.kind, "url": e.url, "excerpt": e.excerpt,
                          "author": e.author, "timestamp": e.provider_timestamp.isoformat()} for e in ev],
            "findings": [finding_out(r, {i.id: i}, {}) for r in findings]}


@router.get("/pull-requests")
def list_prs(project_id: int, state: str | None = None, repo: str | None = None,
             review_state: str | None = None, checks_state: str | None = None, ctx: Ctx = Depends(ws_member)):
    get_project(ctx, project_id)
    qy = select(PullRequest).where(PullRequest.project_id == project_id, PullRequest.workspace_id == ctx.workspace_id)
    for col, val in ((PullRequest.state, state), (PullRequest.repo, repo), (PullRequest.review_state, review_state),
                     (PullRequest.checks_state, checks_state)):
        if val:
            qy = qy.where(col == val)
    names = _names(ctx)
    rows = list(ctx.db.scalars(qy.order_by(PullRequest.number.desc())))
    out = []
    for p in rows:
        d = pr_out(p, names)
        d["checks"] = [{"name": c.name, "status": c.status, "conclusion": c.conclusion, "required": c.required}
                       for c in ctx.db.scalars(select(PullRequestCheck).where(PullRequestCheck.pull_request_id == p.id))]
        out.append(d)
    repos = sorted(set(ctx.db.scalars(select(PullRequest.repo).where(PullRequest.project_id == project_id))))
    return {"items": out, "repos": repos}


@router.get("/risks")
def list_risks(project_id: int, status: str | None = None, confidence: str | None = None,
               ctx: Ctx = Depends(ws_member)):
    get_project(ctx, project_id)
    qy = select(RiskFinding).where(RiskFinding.project_id == project_id, RiskFinding.workspace_id == ctx.workspace_id)
    if status:
        qy = qy.where(RiskFinding.status == status)
    if confidence:
        qy = qy.where(RiskFinding.confidence == confidence)
    rows = list(ctx.db.scalars(qy.order_by(RiskFinding.detected_at.desc())))
    issues = {i.id: i for i in ctx.db.scalars(select(Issue).where(Issue.project_id == project_id))}
    prs = {p.id: p for p in ctx.db.scalars(select(PullRequest).where(PullRequest.project_id == project_id))}
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted([finding_out(r, issues, prs) for r in rows], key=lambda r: (order[r["severity"]], r["id"]))


@router.post("/risks/scan")
def scan(project_id: int, ctx: Ctx = Depends(ws_manager)):
    project = get_project(ctx, project_id)
    res = scan_project(ctx.db, project)
    audit(ctx.db, ctx.workspace_id, "risks.scanned", actor_user_id=ctx.user.id, payload=res)
    ctx.db.commit()
    return res


class FindingStatusIn(BaseModel):
    status: str = Field(pattern="^(open|acknowledged|resolved|dismissed)$")


@router.patch("/risks/{finding_id}")
def set_finding_status(project_id: int, finding_id: int, body: FindingStatusIn, ctx: Ctx = Depends(ws_manager)):
    get_project(ctx, project_id)
    r = ctx.db.scalar(select(RiskFinding).where(RiskFinding.id == finding_id, RiskFinding.project_id == project_id,
                                                RiskFinding.workspace_id == ctx.workspace_id))
    if r is None:
        raise HTTPException(404, "Finding not found")
    r.status, r.status_updated_by, r.status_updated_at = body.status, ctx.user.id, utcnow()
    audit(ctx.db, ctx.workspace_id, "risk.status_changed", actor_user_id=ctx.user.id, target_type="risk_finding",
          target_id=r.id, payload={"status": body.status})
    ctx.db.commit()
    return {"id": r.id, "status": r.status}


@router.get("/evidence/{evidence_id}")
def get_evidence(project_id: int, evidence_id: int, ctx: Ctx = Depends(ws_member)):
    get_project(ctx, project_id)
    e = ctx.db.scalar(select(Evidence).where(Evidence.id == evidence_id, Evidence.project_id == project_id,
                                             Evidence.workspace_id == ctx.workspace_id))
    if e is None:
        raise HTTPException(404, "Evidence not found")
    return {"id": e.id, "title": e.title, "kind": e.kind, "provider": e.provider, "url": e.url,
            "excerpt": e.excerpt, "author": e.author, "timestamp": e.provider_timestamp.isoformat(),
            "retrieved_at": e.retrieved_at.isoformat(), "is_demo": e.url.startswith("https://demo.invalid")}
