"""Idempotent import of normalised connector records. Re-running the same batch creates no duplicates:
rows are matched on (workspace, provider, external_id) and backed by unique constraints.
"""
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.integrations.base import SyncBatch
from app.models import (Evidence, ExternalIdentity, Issue, IssueLink, Project, PullRequest, PullRequestCheck,
                        PullRequestReview, Sprint)


def _dt(v) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(v)


def _date(v) -> date | None:
    return date.fromisoformat(v) if isinstance(v, str) else v


def _get_or_create(db: Session, model, where: dict, stats: dict, stat_key: str, **values):
    obj = db.scalar(select(model).filter_by(**where))
    if obj is None:
        obj = model(**where, **values)
        db.add(obj)
        stats[stat_key + "_created"] = stats.get(stat_key + "_created", 0) + 1
    else:
        for k, v in values.items():
            setattr(obj, k, v)
        stats[stat_key + "_updated"] = stats.get(stat_key + "_updated", 0) + 1
    db.flush()
    return obj


def derive_review_state(reviews: list[dict]) -> str:
    latest: dict[str, str] = {}
    for r in sorted(reviews, key=lambda r: r["submitted_at"]):
        if r["state"] in ("approved", "changes_requested"):
            latest[r["reviewer"]] = r["state"]
    if "changes_requested" in latest.values():
        return "changes_requested"
    if "approved" in latest.values():
        return "approved"
    return "awaiting_review"


def derive_checks_state(checks: list[dict]) -> str:
    if not checks:
        return "none"
    if any(c["status"] == "completed" and c.get("conclusion") == "failure" for c in checks):
        return "failing"
    if any(c["status"] != "completed" for c in checks):
        return "pending"
    return "passing"


def import_batch(db: Session, project: Project, provider: str, batch: SyncBatch, now: datetime | None = None) -> dict:
    now = now or utcnow()
    ws_id = project.workspace_id
    r = batch.records
    stats: dict = {}
    idents: dict[str, ExternalIdentity] = {}

    def ident(ext_id: str | None) -> ExternalIdentity | None:
        if not ext_id:
            return None
        if ext_id not in idents:
            row = db.scalar(select(ExternalIdentity).where(
                ExternalIdentity.workspace_id == ws_id, ExternalIdentity.provider == provider,
                ExternalIdentity.external_id == ext_id))
            if row is None:  # unknown person: create an unresolved identity, never guess a link
                row = ExternalIdentity(workspace_id=ws_id, provider=provider, external_id=ext_id,
                                       display_name=ext_id, status="unresolved")
                db.add(row)
                db.flush()
            idents[ext_id] = row
        return idents[ext_id]

    for p in r.get("identities", []):
        row = db.scalar(select(ExternalIdentity).where(
            ExternalIdentity.workspace_id == ws_id, ExternalIdentity.provider == provider,
            ExternalIdentity.external_id == p["external_id"]))
        if row is None:
            row = ExternalIdentity(workspace_id=ws_id, provider=provider, external_id=p["external_id"],
                                   display_name=p["display_name"], email=p.get("email"), status="unresolved")
            db.add(row)
            stats["identities_created"] = stats.get("identities_created", 0) + 1
        else:
            row.display_name, row.email = p["display_name"], p.get("email")  # never touches user_id/status
        db.flush()
        idents[p["external_id"]] = row

    sprints: dict[str, Sprint] = {}
    for s in r.get("sprints", []):
        sprints[s["external_id"]] = _get_or_create(
            db, Sprint, {"project_id": project.id, "provider": provider, "external_id": s["external_id"]}, stats,
            "sprints", workspace_id=ws_id, name=s["name"], goal=s.get("goal", ""), start_date=_date(s["start_date"]),
            end_date=_date(s["end_date"]), state=s.get("state", "active"), url=s.get("url"), last_synced_at=now)

    for i in r.get("issues", []):
        a = ident(i.get("assignee"))
        issue = _get_or_create(
            db, Issue, {"workspace_id": ws_id, "provider": provider, "external_id": i["external_id"]}, stats, "issues",
            project_id=project.id, sprint_id=sprints[i["sprint"]].id if i.get("sprint") in sprints else None,
            key=i["key"], title=i["title"], description=i.get("description", ""), status=i["status"],
            status_category=i["status_category"], priority=i.get("priority"),
            assignee_identity_id=a.id if a else None, is_blocked=i.get("is_blocked", False),
            blocked_reason=i.get("blocked_reason"), labels=i.get("labels", []), story_points=i.get("story_points"),
            due_date=_date(i.get("due_date")), url=i["url"], provider_created_at=_dt(i["created_at"]),
            provider_updated_at=_dt(i["updated_at"]), last_activity_at=_dt(i["last_activity_at"]), last_synced_at=now)
        for key in i.get("blocked_by", []):
            _get_or_create(db, IssueLink, {"issue_id": issue.id, "target_key": key, "link_type": "blocked_by"},
                           stats, "links", workspace_id=ws_id)
        _evidence(db, ws_id, project.id, provider, "issue", i["external_id"], i["key"], i["url"],
                  f"{i['key']}: {i['title']}",
                  f"Status: {i['status']}. Assignee: {a.display_name if a else 'unassigned'}. "
                  + (f"Blocked: {i['blocked_reason']}. " if i.get("is_blocked") else "")
                  + (f"Due {i['due_date']}. " if i.get("due_date") else "") + (i.get("description") or ""),
                  a.display_name if a else None, _dt(i["updated_at"]), now)

    for p in r.get("pull_requests", []):
        author = ident(p.get("author"))
        reviews, checks = p.get("reviews", []), p.get("checks", [])
        pr = _get_or_create(
            db, PullRequest, {"workspace_id": ws_id, "provider": provider, "external_id": p["external_id"]}, stats,
            "pull_requests", project_id=project.id, repo=p["repo"], number=p["number"], title=p["title"],
            description=p.get("description", ""), author_identity_id=author.id if author else None, state=p["state"],
            draft=p.get("draft", False), review_state=derive_review_state(reviews),
            checks_state=derive_checks_state(checks), issue_key=p.get("issue_key"), url=p["url"],
            provider_created_at=_dt(p["created_at"]), provider_updated_at=_dt(p["updated_at"]),
            review_requested_at=_dt(p.get("review_requested_at")), last_synced_at=now)
        for rv in reviews:
            rr = ident(rv["reviewer"])
            _get_or_create(db, PullRequestReview, {"pull_request_id": pr.id, "external_id": rv["external_id"]}, stats,
                           "reviews", workspace_id=ws_id, reviewer_identity_id=rr.id if rr else None,
                           state=rv["state"], submitted_at=_dt(rv["submitted_at"]))
        for c in checks:
            _get_or_create(db, PullRequestCheck, {"pull_request_id": pr.id, "name": c["name"]}, stats, "checks",
                           workspace_id=ws_id, status=c["status"], conclusion=c.get("conclusion"),
                           required=c.get("required", False), url=c.get("url"))
        failing = [c["name"] for c in checks if c.get("conclusion") == "failure"]
        _evidence(db, ws_id, project.id, provider, "pull_request", p["external_id"], p.get("issue_key"), p["url"],
                  f"PR #{p['number']} ({p['repo']}): {p['title']}",
                  f"State: {p['state']}{' (draft)' if p.get('draft') else ''}. Review: {pr.review_state}. "
                  f"Checks: {pr.checks_state}" + (f" (failing: {', '.join(failing)})" if failing else "") + ".",
                  author.display_name if author else None, _dt(p["updated_at"]), now)

    for c in r.get("comments", []):
        _evidence(db, ws_id, project.id, c["provider"], c["kind"], c["external_id"], c.get("ref_key"), c["url"],
                  c["title"], c["excerpt"], (ident(c.get("author")).display_name if c.get("author") else None),
                  _dt(c["timestamp"]), now)
    db.flush()
    return stats


def _evidence(db, ws_id, project_id, provider, kind, external_id, ref_key, url, title, excerpt, author, ts, now):
    ev = db.scalar(select(Evidence).where(Evidence.workspace_id == ws_id, Evidence.provider == provider,
                                          Evidence.kind == kind, Evidence.external_id == external_id))
    vals = dict(project_id=project_id, ref_key=ref_key, url=url, title=title[:500], excerpt=excerpt, author=author,
                provider_timestamp=ts, retrieved_at=now)
    if ev is None:
        db.add(Evidence(workspace_id=ws_id, provider=provider, kind=kind, external_id=external_id, **vals))
    else:
        for k, v in vals.items():
            setattr(ev, k, v)
    db.flush()
