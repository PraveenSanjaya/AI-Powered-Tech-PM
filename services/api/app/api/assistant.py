from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.llm import get_provider
from app.api.workspaces import get_project
from app.auth.deps import Ctx, ws_manager, ws_member
from app.core.time import utcnow
from app.models import Conversation, MemoryItem, Message, SummarySnapshot, WorkflowRun
from app.services import summary as S
from app.services.audit import audit
from app.workflows.assistant import run_assistant

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["assistant"])


class ConversationIn(BaseModel):
    project_id: int
    sprint_id: int | None = None


class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _conv(ctx: Ctx, conversation_id: int) -> Conversation:
    c = ctx.db.scalar(select(Conversation).where(
        Conversation.id == conversation_id, Conversation.workspace_id == ctx.workspace_id,
        Conversation.user_id == ctx.user.id))  # conversations are private to their owner
    if c is None:
        raise HTTPException(404, "Conversation not found")
    return c


def msg_out(m: Message) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content, "evidence_ids": m.evidence_ids, "meta": m.meta,
            "created_at": m.created_at.isoformat() if m.created_at else None}


@router.post("/conversations", status_code=201)
def create_conversation(body: ConversationIn, ctx: Ctx = Depends(ws_member)):
    get_project(ctx, body.project_id)
    c = Conversation(workspace_id=ctx.workspace_id, user_id=ctx.user.id, project_id=body.project_id,
                     sprint_id=body.sprint_id)
    ctx.db.add(c)
    ctx.db.commit()
    return {"id": c.id, "title": c.title, "project_id": c.project_id, "sprint_id": c.sprint_id}


@router.get("/conversations")
def list_conversations(ctx: Ctx = Depends(ws_member)):
    rows = ctx.db.scalars(select(Conversation).where(Conversation.workspace_id == ctx.workspace_id,
                                                     Conversation.user_id == ctx.user.id)
                          .order_by(Conversation.id.desc()).limit(50))
    return [{"id": c.id, "title": c.title, "project_id": c.project_id} for c in rows]


@router.get("/conversations/{conversation_id}/messages")
def list_messages(conversation_id: int, ctx: Ctx = Depends(ws_member)):
    c = _conv(ctx, conversation_id)
    rows = ctx.db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.id))
    return [msg_out(m) for m in rows]


@router.post("/conversations/{conversation_id}/messages")
def ask(conversation_id: int, body: AskIn, ctx: Ctx = Depends(ws_member)):
    c = _conv(ctx, conversation_id)
    m = run_assistant(ctx.db, conversation=c, user_id=ctx.user.id, role=ctx.role, text=body.text,
                      provider=get_provider())
    return msg_out(m)


@router.delete("/conversations/{conversation_id}", status_code=204)
def clear_conversation(conversation_id: int, ctx: Ctx = Depends(ws_member)):
    c = _conv(ctx, conversation_id)
    ctx.db.delete(c)
    audit(ctx.db, ctx.workspace_id, "conversation.deleted", actor_user_id=ctx.user.id, target_id=conversation_id)
    ctx.db.commit()


@router.post("/workflow-runs/{run_id}/cancel")
def cancel_run(run_id: int, ctx: Ctx = Depends(ws_member)):
    r = ctx.db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.workspace_id == ctx.workspace_id,
                                                WorkflowRun.user_id == ctx.user.id))
    if r is None:
        raise HTTPException(404, "Workflow run not found")
    if r.status == "running":
        r.status = "cancelled"
        ctx.db.commit()
    return {"id": r.id, "status": r.status}


@router.get("/workflow-runs")
def list_runs(ctx: Ctx = Depends(ws_member)):
    rows = ctx.db.scalars(select(WorkflowRun).where(WorkflowRun.workspace_id == ctx.workspace_id)
                          .order_by(WorkflowRun.id.desc()).limit(30))
    return [{"id": r.id, "kind": r.kind, "status": r.status, "tool_calls": r.tool_calls, "steps": r.steps,
             "error": r.error, "started_at": r.started_at.isoformat()} for r in rows]


# ---- summaries --------------------------------------------------------------------------------------------------------
class SummaryIn(BaseModel):
    project_id: int
    sprint_id: int | None = None


@router.post("/summaries", status_code=201)
def generate_summary(body: SummaryIn, ctx: Ctx = Depends(ws_member)):
    project = get_project(ctx, body.project_id)
    sprint = S.active_sprint(ctx.db, project, body.sprint_id)
    f = S.collect(ctx.db, project, sprint)
    status, secs = S.build_sections(f, sprint)
    md = S.render_markdown(secs, f["fresh"], f"Sprint summary — {project.name}", f["fresh"].get("is_demo", False))
    ids = S.evidence_ids_in(secs)
    snap = SummarySnapshot(workspace_id=ctx.workspace_id, project_id=project.id, sprint_id=sprint.id if sprint else None,
                           content={"status": status, "sections": [{"title": s.title, "kind": s.kind, "items": [
                               {"text": i.text, "evidence": i.evidence} for i in s.items]} for s in secs]},
                           markdown=md, evidence_ids=ids, generated_by="demo-template", created_by=ctx.user.id)
    ctx.db.add(snap)
    audit(ctx.db, ctx.workspace_id, "summary.generated", actor_user_id=ctx.user.id, target_type="project",
          target_id=project.id)
    ctx.db.commit()
    return {"id": snap.id, "markdown": md, "status": status, "freshness": f["fresh"],
            "citations": S.resolve_citations(ctx.db, ctx.workspace_id, ids),
            "label": "Deterministic template summary built from synchronized data"}


@router.get("/summaries")
def list_summaries(project_id: int, ctx: Ctx = Depends(ws_member)):
    get_project(ctx, project_id)
    rows = ctx.db.scalars(select(SummarySnapshot).where(SummarySnapshot.workspace_id == ctx.workspace_id,
                                                        SummarySnapshot.project_id == project_id)
                          .order_by(SummarySnapshot.id.desc()).limit(20))
    return [{"id": s.id, "created_at": s.created_at.isoformat(), "generated_by": s.generated_by,
             "markdown": s.markdown} for s in rows]


# ---- memory (user-supplied context, with provenance) --------------------------------------------------------------------
class MemoryIn(BaseModel):
    project_id: int
    kind: str = Field(pattern="^(preference|decision|terminology)$")
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)


@router.get("/memory")
def list_memory(project_id: int, ctx: Ctx = Depends(ws_member)):
    get_project(ctx, project_id)
    rows = ctx.db.scalars(select(MemoryItem).where(MemoryItem.workspace_id == ctx.workspace_id,
                                                   MemoryItem.project_id == project_id).order_by(MemoryItem.kind, MemoryItem.key))
    return [{"id": m.id, "kind": m.kind, "key": m.key, "value": m.value, "provenance": m.provenance,
             "created_at": m.created_at.isoformat()} for m in rows]


@router.put("/memory")
def put_memory(body: MemoryIn, ctx: Ctx = Depends(ws_manager)):
    get_project(ctx, body.project_id)
    m = ctx.db.scalar(select(MemoryItem).where(MemoryItem.workspace_id == ctx.workspace_id,
                                               MemoryItem.project_id == body.project_id, MemoryItem.kind == body.kind,
                                               MemoryItem.key == body.key))
    prov = f"entered by {ctx.user.email} at {utcnow():%Y-%m-%d %H:%M} UTC"
    if m is None:
        m = MemoryItem(workspace_id=ctx.workspace_id, project_id=body.project_id, kind=body.kind, key=body.key,
                       value=body.value, provenance=prov, created_by=ctx.user.id)
        ctx.db.add(m)
    else:
        m.value, m.provenance, m.created_by = body.value, prov, ctx.user.id
    ctx.db.commit()
    return {"id": m.id}


@router.delete("/memory/{memory_id}", status_code=204)
def delete_memory(memory_id: int, ctx: Ctx = Depends(ws_manager)):
    m = ctx.db.scalar(select(MemoryItem).where(MemoryItem.id == memory_id, MemoryItem.workspace_id == ctx.workspace_id))
    if m is None:
        raise HTTPException(404, "Not found")
    ctx.db.delete(m)
    ctx.db.commit()
