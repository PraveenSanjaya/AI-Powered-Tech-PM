"""Development-only demo data. Refuses to run when APP_ENV=production."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import hash_password
from app.core.config import get_settings
from app.models import (AutomationSchedule, IntegrationConnection, Membership, Project, User, Workspace)
from app.services.audit import audit
from app.services.sync import run_sync

DEMO_PASSWORD = "demo-password-123"  # development-only credential, documented in the README
DEMO_USERS = [("admin@demo.example", "Dana Admin", "admin"), ("manager@demo.example", "Morgan Manager", "manager"),
              ("member@demo.example", "Max Member", "member")]


def install_demo_connections(db: Session, workspace_id: int, admin_user_id: int) -> Project:
    """Create the ATLAS demo project and demo connections in a workspace, then sync (idempotent)."""
    project = db.scalar(select(Project).where(Project.workspace_id == workspace_id, Project.key == "ATLAS"))
    if project is None:
        project = Project(workspace_id=workspace_id, key="ATLAS", name="Atlas Checkout Revamp",
                          description="Fictional demo project.")
        db.add(project)
        db.flush()
    caps = {"jira": ["jira_comment"], "github": [], "slack": ["slack_message"]}
    for provider in ("jira", "github", "slack"):
        conn = db.scalar(select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id, IntegrationConnection.provider == provider))
        if conn is None:
            conn = IntegrationConnection(workspace_id=workspace_id, provider=provider, name="default", is_demo=True,
                                         status="connected", scopes=["demo"], capabilities=caps[provider],
                                         config={"project_key": "ATLAS"}, created_by=admin_user_id)
            db.add(conn)
            db.flush()
        run_sync(db, conn, project, scan=provider == "slack")  # slack is last: scan once all data is present
    return project


def seed_demo(db: Session) -> dict:
    if get_settings().app_env == "production":
        raise RuntimeError("Refusing to seed demo data when APP_ENV=production")
    ws = db.scalar(select(Workspace).where(Workspace.slug == "atlas-demo"))
    if ws is None:
        ws = Workspace(name="Atlas Demo Workspace (fictional)", slug="atlas-demo", settings={})
        db.add(ws)
        db.flush()
    users = {}
    for email, name, role in DEMO_USERS:
        u = db.scalar(select(User).where(User.email == email))
        if u is None:
            u = User(email=email, name=name, password_hash=hash_password(DEMO_PASSWORD))
            db.add(u)
            db.flush()
        users[role] = u
        if not db.scalar(select(Membership).where(Membership.workspace_id == ws.id, Membership.user_id == u.id)):
            db.add(Membership(workspace_id=ws.id, user_id=u.id, role=role))
    project = install_demo_connections(db, ws.id, users["admin"].id)
    if not db.scalar(select(AutomationSchedule).where(AutomationSchedule.workspace_id == ws.id)):
        db.add(AutomationSchedule(workspace_id=ws.id, project_id=project.id, kind="daily_summary",
                                  timezone="Asia/Colombo", time_of_day="09:00", enabled=False, destination={},
                                  policy={}, created_by=users["manager"].id))
    audit(db, ws.id, "demo.seeded", actor_type="system")
    db.commit()
    return {"workspace_id": ws.id, "project_id": project.id}
