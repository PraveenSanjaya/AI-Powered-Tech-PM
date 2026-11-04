"""Typed tools the assistant workflow may call. There is deliberately no shell, SQL, HTTP or credential tool.

Every call: role check -> argument validation -> call-budget check -> execution scoped to the context's workspace
and project. Evidence ids returned are tracked so answers can only cite what was actually retrieved.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utcnow
from app.models import (Evidence, Issue, Project, PullRequest, PullRequestCheck, RiskFinding,
                        Sprint)
from app.services import actions as action_svc
from app.services import summary as S

ROLE_RANK = {"member": 1, "manager": 2, "admin": 3}


class ToolError(Exception):
    pass


class ToolLimitError(ToolError):
    pass


@dataclass
class ToolContext:
    db: Session
    workspace_id: int
    project: Project
    sprint: Sprint | None
    user_id: int
    role: str
    workflow_run_id: int | None = None
    max_calls: int = field(default_factory=lambda: get_settings().workflow_max_tool_calls)
    calls: int = 0
    retrieved: set[int] = field(default_factory=set)
    log: list[dict] = field(default_factory=list)


# ---- argument models ---------------------------------------------------------------------------------------------
class ListIssuesArgs(BaseModel):
    status_category: str | None = Field(None, pattern="^(todo|in_progress|done)$")
    assignee: str | None = Field(None, max_length=200)
    blocked_only: bool = False


class IssueKeyArgs(BaseModel):
    key: str = Field(max_length=50, pattern=r"^[A-Za-z][A-Za-z0-9]*-\d+$")


class ListPRArgs(BaseModel):
    needs_review: bool = False
    failing_checks: bool = False


class PRArgs(BaseModel):
    number: int
    repo: str | None = Field(None, max_length=200)


class SearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(10, ge=1, le=25)


class FindingsArgs(BaseModel):
    confidence: str | None = Field(None, pattern="^(confirmed|likely|possible)$")


class RecentArgs(BaseModel):
    hours: int = Field(72, ge=1, le=24 * 30)


class CalendarArgs(BaseModel):
    attendees: list[str] = Field(default_factory=list, max_length=20)
    timezone: str = "UTC"


class ProposeJiraArgs(BaseModel):
    issue_key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]*-\d+$", max_length=50)
    body: str = Field(min_length=1, max_length=8000)
    reason: str = Field("", max_length=1000)
    evidence_ids: list[int] = Field(default_factory=list, max_length=50)


class ProposeSlackArgs(BaseModel):
    channel: str = Field(pattern=r"^#?[a-z0-9][a-z0-9_-]{0,79}$")
    text: str = Field(min_length=1, max_length=4000)
    reason: str = Field("", max_length=1000)
    evidence_ids: list[int] = Field(default_factory=list, max_length=50)


class ProposeCalendarArgs(BaseModel):
    title: str
    start: str
    end: str
    timezone: str
    attendees: list[str]


# ---- implementations -----------------------------------------------------------------------------------------------
def _issue_dict(f, i: Issue) -> dict:
    return {"key": i.key, "title": i.title, "status": i.status, "category": i.status_category,
            "assignee": f["names"].get(i.assignee_identity_id), "blocked": i.is_blocked,
            "blocked_reason": i.blocked_reason, "due_date": i.due_date.isoformat() if i.due_date else None,
            "url": i.url, "last_activity_at": i.last_activity_at.isoformat(), "evidence_ids": S.issue_ev(f, i)}


def list_sprint_issues(c: ToolContext, a: ListIssuesArgs) -> dict:
    f = S.collect(c.db, c.project, c.sprint)
    rows = [i for i in f["issues"]
            if (not a.status_category or i.status_category == a.status_category)
            and (not a.blocked_only or i.is_blocked)
            and (not a.assignee or a.assignee.lower() in (f["names"].get(i.assignee_identity_id) or "").lower())]
    out = [_issue_dict(f, i) for i in rows]
    return {"data": out, "evidence_ids": [e for r in out for e in r["evidence_ids"]]}


def get_issue_details(c: ToolContext, a: IssueKeyArgs) -> dict:
    f = S.collect(c.db, c.project, c.sprint)
    i = next((x for x in f["issues"] if x.key.lower() == a.key.lower()), None)
    if i is None:
        return {"data": None, "evidence_ids": [], "note": f"{a.key} not found in this project/sprint"}
    ev = list(c.db.scalars(select(Evidence).where(Evidence.project_id == c.project.id, Evidence.ref_key == i.key)))
    d = _issue_dict(f, i)
    d["related_evidence"] = [{"id": e.id, "title": e.title, "kind": e.kind} for e in ev]
    return {"data": d, "evidence_ids": d["evidence_ids"] + [e.id for e in ev]}


def list_pull_requests(c: ToolContext, a: ListPRArgs) -> dict:
    f = S.collect(c.db, c.project, c.sprint)
    rows = [p for p in f["prs"] if (not a.needs_review or (not p.draft and p.review_state == "awaiting_review"))
            and (not a.failing_checks or p.checks_state == "failing")]
    out = [{"number": p.number, "repo": p.repo, "title": p.title, "draft": p.draft, "review_state": p.review_state,
            "checks_state": p.checks_state, "issue_key": p.issue_key, "url": p.url, "evidence_ids": S.pr_ev(f, p)}
           for p in rows]
    return {"data": out, "evidence_ids": [e for r in out for e in r["evidence_ids"]]}


def get_pull_request_checks(c: ToolContext, a: PRArgs) -> dict:
    q = select(PullRequest).where(PullRequest.project_id == c.project.id, PullRequest.number == a.number)
    if a.repo:
        q = q.where(PullRequest.repo == a.repo)
    p = c.db.scalars(q).first()
    if p is None:
        return {"data": None, "evidence_ids": [], "note": "Pull request not found"}
    checks = c.db.scalars(select(PullRequestCheck).where(PullRequestCheck.pull_request_id == p.id))
    f = S.collect(c.db, c.project, c.sprint)
    return {"data": [{"name": k.name, "status": k.status, "conclusion": k.conclusion, "required": k.required}
                     for k in checks], "evidence_ids": S.pr_ev(f, p)}


def search_project_evidence(c: ToolContext, a: SearchArgs) -> dict:
    vec = func.to_tsvector("english", Evidence.title + " " + Evidence.excerpt)
    q = select(Evidence).where(Evidence.project_id == c.project.id, Evidence.workspace_id == c.workspace_id)
    rows = list(c.db.scalars(q.where(vec.op("@@")(func.plainto_tsquery("english", a.query)))
                             .order_by(Evidence.provider_timestamp.desc()).limit(a.limit)))
    if not rows:  # fall back to substring match for identifiers such as ATLAS-101
        like = f"%{a.query}%"
        rows = list(c.db.scalars(q.where(or_(Evidence.title.ilike(like), Evidence.excerpt.ilike(like),
                                             Evidence.ref_key.ilike(like)))
                                 .order_by(Evidence.provider_timestamp.desc()).limit(a.limit)))
    return {"data": [{"id": e.id, "title": e.title, "kind": e.kind, "url": e.url} for e in rows],
            "evidence_ids": [e.id for e in rows]}


def get_risk_findings(c: ToolContext, a: FindingsArgs) -> dict:
    q = select(RiskFinding).where(RiskFinding.project_id == c.project.id, RiskFinding.workspace_id == c.workspace_id,
                                  RiskFinding.status.in_(("open", "acknowledged")))
    if a.confidence:
        q = q.where(RiskFinding.confidence == a.confidence)
    rows = list(c.db.scalars(q.order_by(RiskFinding.id)))
    return {"data": [{"id": r.id, "type": r.type, "severity": r.severity, "confidence": r.confidence,
                      "explanation": r.explanation, "method": r.method, "evidence_ids": r.evidence_ids} for r in rows],
            "evidence_ids": [e for r in rows for e in r.evidence_ids]}


def list_recent_changes(c: ToolContext, a: RecentArgs) -> dict:
    since = utcnow() - timedelta(hours=a.hours)
    rows = list(c.db.scalars(select(Evidence).where(
        Evidence.project_id == c.project.id, Evidence.workspace_id == c.workspace_id,
        Evidence.provider_timestamp >= since).order_by(Evidence.provider_timestamp.desc()).limit(50)))
    return {"data": [{"id": e.id, "title": e.title, "kind": e.kind, "timestamp": e.provider_timestamp.isoformat(),
                      "excerpt": e.excerpt[:200]} for e in rows], "evidence_ids": [e.id for e in rows]}


def get_calendar_availability(c: ToolContext, a: CalendarArgs) -> dict:
    return {"data": None, "evidence_ids": [], "available": False,
            "note": "Calendar integration is not implemented in this build (Stage 3)."}


def _check_evidence(c: ToolContext, ids: list[int]) -> list[int]:
    valid = set(c.db.scalars(select(Evidence.id).where(Evidence.workspace_id == c.workspace_id,
                                                       Evidence.project_id == c.project.id, Evidence.id.in_(ids))))
    return [i for i in ids if i in valid]


def propose_jira_comment(c: ToolContext, a: ProposeJiraArgs) -> dict:
    issue = c.db.scalar(select(Issue).where(Issue.project_id == c.project.id, func.lower(Issue.key) == a.issue_key.lower()))
    if issue is None:
        raise ToolError(f"Issue {a.issue_key} not found in this project")
    p = action_svc.create_proposal(
        c.db, project=c.project, user_id=c.user_id, action_type="jira_comment", target={"issue_key": issue.key},
        payload={"body": a.body}, reason=a.reason, evidence_ids=_check_evidence(c, a.evidence_ids),
        workflow_run_id=c.workflow_run_id, system="assistant")
    return {"data": {"proposal_id": p.id, "status": p.status}, "evidence_ids": p.evidence_ids}


def propose_slack_update(c: ToolContext, a: ProposeSlackArgs) -> dict:
    p = action_svc.create_proposal(
        c.db, project=c.project, user_id=c.user_id, action_type="slack_message",
        target={"channel": a.channel if a.channel.startswith("#") else "#" + a.channel}, payload={"text": a.text},
        reason=a.reason, evidence_ids=_check_evidence(c, a.evidence_ids), workflow_run_id=c.workflow_run_id,
        system="assistant")
    return {"data": {"proposal_id": p.id, "status": p.status}, "evidence_ids": p.evidence_ids}


def propose_calendar_event(c: ToolContext, a: ProposeCalendarArgs) -> dict:
    return {"data": None, "evidence_ids": [], "available": False,
            "note": "Calendar integration is not implemented in this build (Stage 3)."}


@dataclass
class Tool:
    name: str
    model: type[BaseModel]
    fn: Callable
    min_role: str = "member"
    writes: bool = False


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("list_sprint_issues", ListIssuesArgs, list_sprint_issues),
    Tool("get_issue_details", IssueKeyArgs, get_issue_details),
    Tool("list_pull_requests", ListPRArgs, list_pull_requests),
    Tool("get_pull_request_checks", PRArgs, get_pull_request_checks),
    Tool("search_project_evidence", SearchArgs, search_project_evidence),
    Tool("get_risk_findings", FindingsArgs, get_risk_findings),
    Tool("list_recent_changes", RecentArgs, list_recent_changes),
    Tool("get_calendar_availability", CalendarArgs, get_calendar_availability, "manager"),
    Tool("propose_jira_comment", ProposeJiraArgs, propose_jira_comment, "manager", True),
    Tool("propose_slack_update", ProposeSlackArgs, propose_slack_update, "manager", True),
    Tool("propose_calendar_event", ProposeCalendarArgs, propose_calendar_event, "manager", True),
]}


def call_tool(c: ToolContext, name: str, args: dict) -> dict:
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"Unknown tool: {name}")
    if ROLE_RANK[c.role] < ROLE_RANK[tool.min_role]:
        raise ToolError(f"Tool {name} requires the {tool.min_role} role")
    if c.calls >= c.max_calls:
        raise ToolLimitError("Tool-call budget exhausted for this workflow")
    try:
        parsed = tool.model(**args)
    except ValidationError as exc:
        raise ToolError(f"Invalid arguments for {name}: {exc.errors()[0]['msg']}") from exc
    c.calls += 1
    result = tool.fn(c, parsed)
    c.retrieved.update(result.get("evidence_ids", []))
    c.log.append({"tool": name, "writes": tool.writes, "evidence_count": len(result.get("evidence_ids", []))})
    return result
