"""Evidence-backed sprint facts and the deterministic (template) sprint summary.

Every item carries the evidence ids that support it; items without a backing evidence record say so explicitly.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import (Evidence, ExternalIdentity, IntegrationConnection, Issue, Project, PullRequest, RiskFinding,
                        Sprint)

STALE_AFTER = timedelta(hours=24)
BLOCKER_TYPES = {"blocked_issue", "unresolved_dependency"}


@dataclass
class Item:
    text: str
    evidence: list[int] = field(default_factory=list)
    claim: bool = True  # False for derived counts/placeholders needing no single source record


@dataclass
class Section:
    title: str
    kind: str  # fact | inference | recommendation
    items: list[Item]


def freshness(db: Session, workspace_id: int, now: datetime | None = None) -> dict:
    now = now or utcnow()
    conns = list(db.scalars(select(IntegrationConnection).where(
        IntegrationConnection.workspace_id == workspace_id, IntegrationConnection.status != "disconnected")))
    times = [c.last_success_at for c in conns if c.last_success_at]
    if not conns or not times:
        return {"last_synced_at": None, "stale": True, "label": "No successful synchronization recorded.",
                "is_demo": any(c.is_demo for c in conns)}
    oldest = min(times)
    return {"last_synced_at": oldest.isoformat(), "stale": (now - oldest) > STALE_AFTER,
            "label": f"Oldest source last synchronized {_ago(now - oldest)} ago"
                     + (" (STALE)" if (now - oldest) > STALE_AFTER else "") + ".",
            "is_demo": any(c.is_demo for c in conns)}


def _ago(td: timedelta) -> str:
    h = int(td.total_seconds() // 3600)
    return f"{h // 24}d {h % 24}h" if h >= 24 else f"{h}h" if h else f"{int(td.total_seconds() // 60)}m"


def active_sprint(db: Session, project: Project, sprint_id: int | None = None) -> Sprint | None:
    if sprint_id:
        return db.scalar(select(Sprint).where(Sprint.id == sprint_id, Sprint.project_id == project.id))
    return db.scalar(select(Sprint).where(Sprint.project_id == project.id, Sprint.state == "active")
                     .order_by(Sprint.start_date.desc()))


def collect(db: Session, project: Project, sprint: Sprint | None) -> dict:
    now = utcnow()
    em = {}
    for p, k, x, i in db.execute(select(Evidence.provider, Evidence.kind, Evidence.external_id, Evidence.id)
                                 .where(Evidence.project_id == project.id)).all():
        em[(p, k, x)] = i
    names = {i.id: i.display_name for i in db.scalars(select(ExternalIdentity).where(
        ExternalIdentity.workspace_id == project.workspace_id))}
    q = select(Issue).where(Issue.project_id == project.id)
    if sprint:
        q = q.where(Issue.sprint_id == sprint.id)
    issues = list(db.scalars(q.order_by(Issue.key)))
    prs = list(db.scalars(select(PullRequest).where(PullRequest.project_id == project.id, PullRequest.state == "open")
                          .order_by(PullRequest.number)))
    findings = list(db.scalars(select(RiskFinding).where(
        RiskFinding.project_id == project.id, RiskFinding.status.in_(("open", "acknowledged")))
        .order_by(RiskFinding.severity.desc(), RiskFinding.id)))
    return {"now": now, "em": em, "names": names, "issues": issues, "prs": prs, "findings": findings,
            "fresh": freshness(db, project.workspace_id, now)}


def issue_ev(f: dict, i: Issue) -> list[int]:
    e = f["em"].get((i.provider, "issue", i.external_id))
    return [e] if e else []


def pr_ev(f: dict, p: PullRequest) -> list[int]:
    e = f["em"].get((p.provider, "pull_request", p.external_id))
    return [e] if e else []


def build_sections(f: dict, sprint: Sprint | None) -> tuple[str, list[Section]]:
    issues, prs, findings, names = f["issues"], f["prs"], f["findings"], f["names"]
    done = [i for i in issues if i.status_category == "done"]
    wip = [i for i in issues if i.status_category == "in_progress"]
    todo = [i for i in issues if i.status_category == "todo"]
    confirmed = [x for x in findings if x.type in BLOCKER_TYPES and x.confidence == "confirmed"]
    risks = [x for x in findings if x not in confirmed and x.type != "pr_needs_review"]
    who = lambda i: names.get(i.assignee_identity_id, "unassigned")  # noqa: E731

    if confirmed or any(x.severity == "high" for x in findings):
        status = "At risk"
        why = (f"{len(confirmed)} confirmed blocker(s) and {sum(1 for x in findings if x.severity == 'high')} "
               "high-severity finding(s) are open.")
    elif findings:
        status, why = "Needs attention", f"{len(findings)} open finding(s), none high severity."
    else:
        status, why = "No open findings", "No rule-based findings are open. This is not a guarantee of delivery."

    s = []
    head = [Item(f"Status: {status}. {why} (Derived from the deterministic rules, not a forecast.)", claim=False)]
    if sprint:
        days = (sprint.end_date - f["now"].date()).days
        head.append(Item(f"{sprint.name}: {sprint.start_date} to {sprint.end_date} "
                         f"({'ends in ' + str(days) + ' day(s)' if days >= 0 else 'ended ' + str(-days) + ' day(s) ago'}). "
                         f"Goal: {sprint.goal or 'not set'}", claim=False))
    head.append(Item(f"{len(done)} of {len(issues)} issues are done; {len(wip)} in progress; {len(todo)} not started.", claim=False))
    s.append(Section("Overall status", "fact", head))
    s.append(Section("Completed work", "fact", [Item(f"{i.key} {i.title} ({who(i)})", issue_ev(f, i)) for i in done]
                     or [Item("No issues are done yet.", claim=False)]))
    s.append(Section("Work in progress", "fact", [Item(f"{i.key} {i.title} — {i.status}, {who(i)}", issue_ev(f, i))
                                                  for i in wip] or [Item("Nothing is in progress.", claim=False)]))
    s.append(Section("Confirmed blockers", "fact", [Item(x.explanation, x.evidence_ids) for x in confirmed]
                     or [Item("No confirmed blockers.", claim=False)]))
    s.append(Section("Potential delivery risks", "inference", [
        Item(f"{x.explanation} [{x.confidence}, {x.severity}]", x.evidence_ids) for x in risks]
        or [Item("No potential risks flagged.", claim=False)]))
    need = [p for p in prs if not p.draft and p.review_state == "awaiting_review"]
    failing = [p for p in prs if p.checks_state == "failing"]
    s.append(Section("Pull requests needing attention", "fact",
                     [Item(f"PR #{p.number} {p.title} ({p.repo}) — awaiting review", pr_ev(f, p)) for p in need]
                     + [Item(f"PR #{p.number} {p.title} ({p.repo}) — failing checks", pr_ev(f, p)) for p in failing]
                     or [Item("No pull requests need attention.", claim=False)]))
    recs = []
    for x in confirmed:
        recs.append(Item(f"Resolve the blocker: {x.explanation.split(':')[0]}. Consider a Jira comment or a short "
                         "blocker-resolution meeting with the owners.", x.evidence_ids))
    for p in need:
        recs.append(Item(f"Assign a reviewer to PR #{p.number} ({p.repo}).", pr_ev(f, p)))
    for p in failing:
        recs.append(Item(f"Fix or triage failing checks on PR #{p.number} ({p.repo}).", pr_ev(f, p)))
    s.append(Section("Suggested next actions", "recommendation", recs or [Item("No actions suggested.", claim=False)]))
    return status, s


def render_markdown(sections: list[Section], fresh: dict, title: str, demo: bool) -> str:
    out = [f"## {title}"]
    if demo:
        out.append("_Demo data and deterministic template output — not a live provider and not LLM-generated._")
    for sec in sections:
        out.append(f"\n### {sec.title}" + ("  \n_(inference)_" if sec.kind == "inference" else ""))
        for it in sec.items:
            refs = " ".join(f"[EV-{e}]" for e in it.evidence)
            if it.evidence or not it.claim:
                out.append(f"- {it.text} {refs}".rstrip())
            else:
                out.append(f"- {it.text} _(no supporting evidence record)_")
    out.append(f"\n### Data freshness\n- {fresh['label']}")
    return "\n".join(out)


def evidence_ids_in(sections: list[Section]) -> list[int]:
    seen: list[int] = []
    for sec in sections:
        for it in sec.items:
            for e in it.evidence:
                if e not in seen:
                    seen.append(e)
    return seen


def resolve_citations(db: Session, workspace_id: int, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    rows = db.scalars(select(Evidence).where(Evidence.workspace_id == workspace_id, Evidence.id.in_(ids)))
    by = {e.id: e for e in rows}
    return [{"id": i, "title": by[i].title, "url": by[i].url, "provider": by[i].provider, "kind": by[i].kind,
             "timestamp": by[i].provider_timestamp.isoformat(), "excerpt": by[i].excerpt[:300],
             "is_demo": by[i].url.startswith("https://demo.invalid")} for i in ids if i in by]
