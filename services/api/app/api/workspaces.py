from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.auth.deps import Ctx, ws_admin, ws_manager, ws_member
from app.models import ExternalIdentity, Membership, Project, Sprint, User, Workspace
from app.services.audit import audit
from app.services.risk import DEFAULT_THRESHOLDS, get_thresholds, validate_thresholds

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace"])


def get_project(ctx: Ctx, project_id: int) -> Project:
    p = ctx.db.scalar(select(Project).where(Project.id == project_id, Project.workspace_id == ctx.workspace_id))
    if p is None:
        raise HTTPException(404, "Project not found")
    return p


def sprint_out(s: Sprint) -> dict:
    return {"id": s.id, "name": s.name, "goal": s.goal, "start_date": s.start_date.isoformat(),
            "end_date": s.end_date.isoformat(), "state": s.state, "url": s.url, "provider": s.provider}


@router.get("/settings")
def get_settings_(ctx: Ctx = Depends(ws_member)):
    ws = ctx.db.get(Workspace, ctx.workspace_id)
    return {"id": ws.id, "name": ws.name, "role": ctx.role, "risk_thresholds": get_thresholds(ws),
            "defaults": DEFAULT_THRESHOLDS}


class ThresholdsIn(BaseModel):
    risk_thresholds: dict


@router.patch("/settings")
def update_settings(body: ThresholdsIn, ctx: Ctx = Depends(ws_admin)):
    try:
        clean = validate_thresholds(body.risk_thresholds)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    ws = ctx.db.get(Workspace, ctx.workspace_id)
    ws.settings = {**(ws.settings or {}), "risk": {**(ws.settings or {}).get("risk", {}), **clean}}
    audit(ctx.db, ws.id, "settings.updated", actor_user_id=ctx.user.id, payload={"risk_thresholds": clean})
    ctx.db.commit()
    return {"risk_thresholds": get_thresholds(ws)}


# ---- members --------------------------------------------------------------------------------------------------------
class MemberIn(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(admin|manager|member)$")


@router.get("/members")
def list_members(ctx: Ctx = Depends(ws_member)):
    rows = ctx.db.execute(select(User, Membership.role).join(Membership, Membership.user_id == User.id)
                          .where(Membership.workspace_id == ctx.workspace_id)).all()
    return [{"user_id": u.id, "email": u.email, "name": u.name, "role": r} for u, r in rows]


@router.put("/members")
def upsert_member(body: MemberIn, ctx: Ctx = Depends(ws_admin)):
    user = ctx.db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        raise HTTPException(404, "No registered user with that email")
    m = ctx.db.scalar(select(Membership).where(Membership.workspace_id == ctx.workspace_id, Membership.user_id == user.id))
    if m is None:
        m = Membership(workspace_id=ctx.workspace_id, user_id=user.id, role=body.role)
        ctx.db.add(m)
    else:
        if m.role == "admin" and body.role != "admin" and ctx.db.scalar(
                select(Membership.id).where(Membership.workspace_id == ctx.workspace_id, Membership.role == "admin",
                                            Membership.id != m.id)) is None:
            raise HTTPException(409, "Workspace must keep at least one admin")
        m.role = body.role
    audit(ctx.db, ctx.workspace_id, "membership.updated", actor_user_id=ctx.user.id, target_type="user",
          target_id=user.id, payload={"role": body.role})
    ctx.db.commit()
    return {"user_id": user.id, "role": body.role}


# ---- projects & sprints ---------------------------------------------------------------------------------------------
class ProjectIn(BaseModel):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)


@router.get("/projects")
def list_projects(ctx: Ctx = Depends(ws_member)):
    rows = ctx.db.scalars(select(Project).where(Project.workspace_id == ctx.workspace_id).order_by(Project.name))
    return [{"id": p.id, "key": p.key, "name": p.name, "description": p.description} for p in rows]


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, ctx: Ctx = Depends(ws_manager)):
    if ctx.db.scalar(select(Project.id).where(Project.workspace_id == ctx.workspace_id, Project.key == body.key)):
        raise HTTPException(409, "Project key already exists")
    p = Project(workspace_id=ctx.workspace_id, **body.model_dump())
    ctx.db.add(p)
    ctx.db.flush()
    audit(ctx.db, ctx.workspace_id, "project.created", actor_user_id=ctx.user.id, target_type="project", target_id=p.id)
    ctx.db.commit()
    return {"id": p.id, "key": p.key, "name": p.name, "description": p.description}


class SprintIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    goal: str = Field("", max_length=2000)
    start_date: date
    end_date: date
    state: str = Field("active", pattern="^(planned|active|closed)$")


@router.get("/projects/{project_id}/sprints")
def list_sprints(project_id: int, ctx: Ctx = Depends(ws_member)):
    get_project(ctx, project_id)
    rows = ctx.db.scalars(select(Sprint).where(Sprint.project_id == project_id).order_by(Sprint.start_date.desc()))
    return [sprint_out(s) for s in rows]


@router.post("/projects/{project_id}/sprints", status_code=201)
def create_sprint(project_id: int, body: SprintIn, ctx: Ctx = Depends(ws_manager)):
    get_project(ctx, project_id)
    if body.end_date < body.start_date:
        raise HTTPException(422, "end_date must not precede start_date")
    s = Sprint(workspace_id=ctx.workspace_id, project_id=project_id, provider="local",
               external_id=f"local-{body.name}-{body.start_date}", **body.model_dump())
    ctx.db.add(s)
    ctx.db.flush()
    audit(ctx.db, ctx.workspace_id, "sprint.created", actor_user_id=ctx.user.id, target_type="sprint", target_id=s.id)
    ctx.db.commit()
    return sprint_out(s)


@router.patch("/projects/{project_id}/sprints/{sprint_id}")
def update_sprint(project_id: int, sprint_id: int, body: SprintIn, ctx: Ctx = Depends(ws_manager)):
    get_project(ctx, project_id)
    s = ctx.db.scalar(select(Sprint).where(Sprint.id == sprint_id, Sprint.project_id == project_id))
    if s is None:
        raise HTTPException(404, "Sprint not found")
    if s.provider != "local":
        raise HTTPException(409, f"Sprint is managed by {s.provider}; edit it there")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    audit(ctx.db, ctx.workspace_id, "sprint.updated", actor_user_id=ctx.user.id, target_type="sprint", target_id=s.id)
    ctx.db.commit()
    return sprint_out(s)


# ---- external identities ----------------------------------------------------------------------------------------------
class ConfirmIdentityIn(BaseModel):
    user_id: int | None


@router.get("/identities")
def list_identities(ctx: Ctx = Depends(ws_member)):
    rows = ctx.db.scalars(select(ExternalIdentity).where(ExternalIdentity.workspace_id == ctx.workspace_id)
                          .order_by(ExternalIdentity.display_name, ExternalIdentity.provider))
    return [{"id": i.id, "provider": i.provider, "external_id": i.external_id, "display_name": i.display_name,
             "email": i.email, "status": i.status, "user_id": i.user_id} for i in rows]


@router.put("/identities/{identity_id}")
def confirm_identity(identity_id: int, body: ConfirmIdentityIn, ctx: Ctx = Depends(ws_manager)):
    """Explicit human confirmation. Never inferred from matching names."""
    ident = ctx.db.scalar(select(ExternalIdentity).where(ExternalIdentity.id == identity_id,
                                                         ExternalIdentity.workspace_id == ctx.workspace_id))
    if ident is None:
        raise HTTPException(404, "Identity not found")
    if body.user_id is not None:
        if not ctx.db.scalar(select(Membership.id).where(Membership.workspace_id == ctx.workspace_id,
                                                         Membership.user_id == body.user_id)):
            raise HTTPException(422, "User is not a member of this workspace")
        ident.user_id, ident.status = body.user_id, "confirmed"
        ident.confirmed_by = ctx.user.id
    else:
        ident.user_id, ident.status, ident.confirmed_by = None, "unresolved", None
    audit(ctx.db, ctx.workspace_id, "identity.mapped", actor_user_id=ctx.user.id, target_type="external_identity",
          target_id=ident.id, payload={"user_id": body.user_id})
    ctx.db.commit()
    return {"id": ident.id, "status": ident.status, "user_id": ident.user_id}
