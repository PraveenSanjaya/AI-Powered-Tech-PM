"""Deterministic risk and blocker rules. No LLM involvement: every finding names its rule and cites evidence.

Missing activity never proves a blocker, so activity-based rules yield `possible` confidence only.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import (Evidence, Issue, IssueLink, Project, PullRequest, PullRequestCheck, RiskFinding, Sprint,
                        Workspace)

DEFAULT_THRESHOLDS = {
    "stale_days": 3,                 # in-progress issue with no activity
    "pr_review_hours": 48,           # open PR waiting for review
    "sprint_end_window_days": 3,     # "approaching sprint end"
    "blocker_keywords": ["blocked", "blocker", "waiting on", "can't proceed", "cannot proceed", "stuck"],
}
THRESHOLD_TYPES = {"stale_days": int, "pr_review_hours": int, "sprint_end_window_days": int, "blocker_keywords": list}

RULES = ["explicit_blocker", "overdue_issue", "stale_in_progress", "pr_awaiting_review", "failed_checks",
         "unresolved_dependency", "sprint_end_pending_work", "slack_blocker_signal"]


def get_thresholds(ws: Workspace) -> dict:
    return {**DEFAULT_THRESHOLDS, **(ws.settings or {}).get("risk", {})}


def validate_thresholds(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k not in THRESHOLD_TYPES:
            raise ValueError(f"Unknown threshold: {k}")
        t = THRESHOLD_TYPES[k]
        if t is int:
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 365 * 24:
                raise ValueError(f"{k} must be a non-negative integer")
        elif t is list:
            if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v) or len(v) > 50:
                raise ValueError(f"{k} must be a list of non-empty strings")
            v = [x.strip().lower() for x in v]
        out[k] = v
    return out


@dataclass
class Candidate:
    type: str
    severity: str
    confidence: str
    method: str
    explanation: str
    key: str  # unique per subject
    issue_id: int | None = None
    pull_request_id: int | None = None
    evidence_ids: list[int] = field(default_factory=list)


def scan_project(db: Session, project: Project, *, now: datetime | None = None) -> dict:
    """Evaluate all rules for the project's active sprint (or all open work if no active sprint) and upsert findings."""
    now = now or utcnow()
    ws = db.get(Workspace, project.workspace_id)
    th = get_thresholds(ws)
    sprint = db.scalar(select(Sprint).where(Sprint.project_id == project.id, Sprint.state == "active")
                       .order_by(Sprint.start_date.desc()))
    q = select(Issue).where(Issue.project_id == project.id, Issue.workspace_id == project.workspace_id)
    if sprint:
        q = q.where(Issue.sprint_id == sprint.id)
    issues = list(db.scalars(q))
    all_issues = {i.key: i for i in db.scalars(select(Issue).where(Issue.project_id == project.id))}
    prs = list(db.scalars(select(PullRequest).where(PullRequest.project_id == project.id, PullRequest.state == "open")))

    ev_rows = list(db.scalars(select(Evidence).where(Evidence.project_id == project.id)))
    ev_by_ref = {(e.provider, e.kind, e.external_id): e for e in ev_rows}

    def issue_ev(i: Issue) -> list[int]:
        e = ev_by_ref.get((i.provider, "issue", i.external_id))
        return [e.id] if e else []

    def pr_ev(p: PullRequest) -> list[int]:
        e = ev_by_ref.get((p.provider, "pull_request", p.external_id))
        return [e.id] if e else []

    cands: list[Candidate] = []
    today = now.date()

    for i in issues:
        if i.status_category == "done":
            continue
        if i.is_blocked:
            cands.append(Candidate("blocked_issue", "high", "confirmed", "explicit_blocker",
                                   f"{i.key} is marked blocked in {i.provider}"
                                   + (f": {i.blocked_reason}" if i.blocked_reason else "."),
                                   f"explicit_blocker:issue:{i.id}", issue_id=i.id, evidence_ids=issue_ev(i)))
        if i.due_date and i.due_date < today:
            days = (today - i.due_date).days
            cands.append(Candidate("overdue_issue", "high" if i.priority in ("High", "Highest") else "medium",
                                   "confirmed", "overdue_issue",
                                   f"{i.key} was due {i.due_date.isoformat()} ({days} day(s) ago) and is still '{i.status}'.",
                                   f"overdue_issue:issue:{i.id}", issue_id=i.id, evidence_ids=issue_ev(i)))
        if i.status_category == "in_progress" and (now - i.last_activity_at) > timedelta(days=th["stale_days"]):
            d = (now - i.last_activity_at).days
            cands.append(Candidate("stale_issue", "medium", "possible", "stale_in_progress",
                                   f"{i.key} is in progress but has no recorded activity for {d} day(s) "
                                   f"(threshold {th['stale_days']}). Inactivity alone does not show the work is blocked.",
                                   f"stale_in_progress:issue:{i.id}", issue_id=i.id, evidence_ids=issue_ev(i)))
        for link in db.scalars(select(IssueLink).where(IssueLink.issue_id == i.id, IssueLink.link_type == "blocked_by")):
            dep = all_issues.get(link.target_key)
            if dep is None:
                cands.append(Candidate("unresolved_dependency", "medium", "possible", "unresolved_dependency",
                                       f"{i.key} depends on {link.target_key}, which is not synchronized so its state is unknown.",
                                       f"unresolved_dependency:issue:{i.id}:{link.target_key}", issue_id=i.id,
                                       evidence_ids=issue_ev(i)))
            elif dep.status_category != "done":
                cands.append(Candidate("unresolved_dependency", "high", "confirmed", "unresolved_dependency",
                                       f"{i.key} is blocked by {dep.key} ('{dep.status}'), which is not done.",
                                       f"unresolved_dependency:issue:{i.id}:{dep.key}", issue_id=i.id,
                                       evidence_ids=issue_ev(i) + issue_ev(dep)))
        if sprint and 0 <= (sprint.end_date - today).days <= th["sprint_end_window_days"]:
            open_prs = [p for p in prs if p.issue_key == i.key]
            if open_prs:
                cands.append(Candidate("sprint_end_pending_work", "medium", "likely", "sprint_end_pending_work",
                                       f"{i.key} is not done and sprint ends {sprint.end_date.isoformat()}; linked PR(s) "
                                       + ", ".join(f"#{p.number}" for p in open_prs) + " are still open.",
                                       f"sprint_end_pending_work:issue:{i.id}", issue_id=i.id,
                                       evidence_ids=issue_ev(i) + [e for p in open_prs for e in pr_ev(p)]))

    for p in prs:
        if p.draft:
            continue
        if p.review_state == "awaiting_review" and p.review_requested_at and \
                (now - p.review_requested_at) > timedelta(hours=th["pr_review_hours"]):
            hrs = int((now - p.review_requested_at).total_seconds() // 3600)
            cands.append(Candidate("pr_needs_review", "medium", "confirmed", "pr_awaiting_review",
                                   f"PR #{p.number} ({p.repo}) has been awaiting review for {hrs}h "
                                   f"(threshold {th['pr_review_hours']}h).",
                                   f"pr_awaiting_review:pr:{p.id}", pull_request_id=p.id, evidence_ids=pr_ev(p)))
        if p.checks_state == "failing":
            failed = list(db.scalars(select(PullRequestCheck).where(
                PullRequestCheck.pull_request_id == p.id, PullRequestCheck.conclusion == "failure")))
            req = [c for c in failed if c.required]
            names = ", ".join(c.name for c in failed) or "unknown check"
            cands.append(Candidate("failed_checks", "high" if req else "medium", "confirmed", "failed_checks",
                                   f"PR #{p.number} ({p.repo}) has failing {'required ' if req else ''}check(s): {names}.",
                                   f"failed_checks:pr:{p.id}", pull_request_id=p.id, evidence_ids=pr_ev(p)))

    # Chat signals: presented as potential findings that need human review.
    if sprint is not None:
        msgs = db.scalars(select(Evidence).where(
            Evidence.project_id == project.id, Evidence.kind == "message",
            Evidence.provider_timestamp >= now - timedelta(days=14)))
        for m in msgs:
            text = (m.excerpt or "").lower()
            if any(k in text for k in th["blocker_keywords"]) and not is_instruction_like(text):
                issue = all_issues.get(m.ref_key) if m.ref_key else None
                if issue and issue.status_category == "done":
                    continue
                cands.append(Candidate("potential_blocker_signal", "low", "possible", "slack_blocker_signal",
                                       "A chat message may describe a blocker"
                                       + (f" related to {m.ref_key}" if m.ref_key else "")
                                       + ". Review it before treating it as a blocker.",
                                       f"slack_blocker_signal:evidence:{m.id}", issue_id=issue.id if issue else None,
                                       evidence_ids=[m.id]))
    return _upsert(db, project, sprint, cands, now)


_INSTRUCTION = re.compile(r"ignore (all |any )?(previous|prior) instructions|system prompt|reveal .*(secret|token)", re.I)


def is_instruction_like(text: str) -> bool:
    """Chat text that tries to steer the system is never promoted into a finding by keyword alone."""
    return bool(_INSTRUCTION.search(text))


def _upsert(db: Session, project: Project, sprint: Sprint | None, cands: list[Candidate], now: datetime) -> dict:
    existing = {f.dedupe_key: f for f in db.scalars(select(RiskFinding).where(
        RiskFinding.project_id == project.id, RiskFinding.workspace_id == project.workspace_id))}
    seen, created, updated = set(), 0, 0
    for c in cands:
        if c.key in seen:
            continue
        seen.add(c.key)
        f = existing.get(c.key)
        if f is None:
            db.add(RiskFinding(workspace_id=project.workspace_id, project_id=project.id,
                               sprint_id=sprint.id if sprint else None, issue_id=c.issue_id,
                               pull_request_id=c.pull_request_id, dedupe_key=c.key, type=c.type,
                               severity=c.severity, confidence=c.confidence, method=c.method,
                               explanation=c.explanation, evidence_ids=c.evidence_ids, status="open",
                               detected_at=now, last_seen_at=now))
            created += 1
        else:
            f.last_seen_at = now
            f.severity, f.confidence, f.explanation, f.evidence_ids = c.severity, c.confidence, c.explanation, c.evidence_ids
            if f.status == "resolved" and f.status_updated_by is None:  # auto-resolved earlier, condition is back
                f.status = "open"
            updated += 1
    auto_resolved = 0
    for key, f in existing.items():
        if key not in seen and f.status in ("open", "acknowledged"):
            f.status, f.status_updated_at, f.status_updated_by = "resolved", now, None  # condition no longer observed
            auto_resolved += 1
    db.flush()
    return {"created": created, "updated": updated, "auto_resolved": auto_resolved}
