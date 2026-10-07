from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.workspaces import get_project
from app.auth.deps import Ctx, ws_manager, ws_member
from app.models import ActionExecution, ActionPayloadVersion, ActionProposal, User
from app.services import actions as svc
from app.services import summary as S

router = APIRouter(prefix="/workspaces/{workspace_id}/actions", tags=["actions"])


def _get(ctx: Ctx, action_id: int) -> ActionProposal:
    p = ctx.db.scalar(select(ActionProposal).where(ActionProposal.id == action_id,
                                                   ActionProposal.workspace_id == ctx.workspace_id))
    if p is None:
        raise HTTPException(404, "Action not found")
    svc.expire_if_needed(ctx.db, p)  # lazily persist expiry so every view agrees
    ctx.db.commit()
    return p


def out(ctx: Ctx, p: ActionProposal, detail: bool = False) -> dict:
    v = svc.latest_version(ctx.db, p)
    requester = ctx.db.get(User, p.requested_by) if p.requested_by else None
    d = {"id": p.id, "provider": p.provider, "action_type": p.action_type, "target": p.target, "status": p.status,
         "reason": p.reason, "version": p.current_version, "payload": v.payload, "payload_hash": v.payload_hash,
         "requested_by": {"id": requester.id, "name": requester.name} if requester else None,
         "proposed_by": p.proposed_by_system, "expires_at": p.expires_at.isoformat(), "is_demo": p.is_demo,
         "approved_version": p.approved_version, "external_id": p.external_id, "external_url": p.external_url,
         "created_at": p.created_at.isoformat(), "evidence_ids": p.evidence_ids,
         "recipients": [p.target.get("channel")] if p.action_type == "slack_message"
         else (v.payload.get("attendees") or [p.target.get("issue_key")]),
         "simulated_notice": "DEMO MODE: executing this action only writes to a local simulation ledger. "
                             "Nothing will be sent to a real service." if p.is_demo else None}
    if detail:
        d["citations"] = S.resolve_citations(ctx.db, ctx.workspace_id, p.evidence_ids)
        d["executions"] = [{"attempt": e.attempt, "status": e.status, "started_at": e.started_at.isoformat(),
                            "result": e.result, "error": e.error} for e in ctx.db.scalars(
            select(ActionExecution).where(ActionExecution.proposal_id == p.id).order_by(ActionExecution.attempt))]
        d["versions"] = [{"version": x.version, "payload": x.payload, "created_at": x.created_at.isoformat()}
                         for x in ctx.db.scalars(select(ActionPayloadVersion).where(
                             ActionPayloadVersion.proposal_id == p.id).order_by(ActionPayloadVersion.version))]
    return d


def _wrap(fn):
    try:
        return fn()
    except svc.ActionError as exc:
        raise HTTPException(exc.status, str(exc))


@router.get("")
def list_actions(status: str | None = None, ctx: Ctx = Depends(ws_member)):
    q = select(ActionProposal).where(ActionProposal.workspace_id == ctx.workspace_id)
    if status:
        q = q.where(ActionProposal.status == status)
    return [out(ctx, p) for p in ctx.db.scalars(q.order_by(ActionProposal.id.desc()).limit(100))]


@router.get("/{action_id}")
def get_action(action_id: int, ctx: Ctx = Depends(ws_member)):
    return out(ctx, _get(ctx, action_id), detail=True)


class ProposeIn(BaseModel):
    project_id: int
    action_type: str
    target: dict
    payload: dict
    reason: str = Field("", max_length=1000)


@router.post("", status_code=201)
def propose(body: ProposeIn, ctx: Ctx = Depends(ws_manager)):
    project = get_project(ctx, body.project_id)

    def go():
        p = svc.create_proposal(ctx.db, project=project, user_id=ctx.user.id, action_type=body.action_type,
                                target=body.target, payload=body.payload, reason=body.reason, evidence_ids=[],
                                system="user")
        ctx.db.commit()
        return out(ctx, p, True)
    return _wrap(go)


class EditIn(BaseModel):
    payload: dict
    target: dict | None = None


@router.put("/{action_id}/payload")
def edit(action_id: int, body: EditIn, ctx: Ctx = Depends(ws_manager)):
    p = _get(ctx, action_id)

    def go():
        svc.edit_payload(ctx.db, p, ctx.user.id, body.payload, body.target)
        ctx.db.commit()
        return out(ctx, p, True)
    return _wrap(go)


class ApproveIn(BaseModel):
    version: int  # the version the human actually reviewed


@router.post("/{action_id}/approve")
def approve(action_id: int, body: ApproveIn, ctx: Ctx = Depends(ws_manager)):
    p = _get(ctx, action_id)

    def go():
        svc.approve(ctx.db, p, ctx.user.id, body.version)
        ctx.db.commit()
        return out(ctx, p, True)
    return _wrap(go)


class RejectIn(BaseModel):
    note: str = Field("", max_length=500)


@router.post("/{action_id}/reject")
def reject(action_id: int, body: RejectIn, ctx: Ctx = Depends(ws_manager)):
    p = _get(ctx, action_id)

    def go():
        svc.reject(ctx.db, p, ctx.user.id, body.note)
        ctx.db.commit()
        return out(ctx, p, True)
    return _wrap(go)


@router.post("/{action_id}/execute")
def execute(action_id: int, ctx: Ctx = Depends(ws_manager)):
    p = _get(ctx, action_id)  # scope check before touching the executor

    def go():
        res = svc.execute(ctx.db, p.id, ctx.user.id)
        ctx.db.commit()
        return out(ctx, res, True)
    return _wrap(go)
