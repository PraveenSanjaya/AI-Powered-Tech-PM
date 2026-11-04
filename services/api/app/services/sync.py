from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.integrations.base import Connector, ProviderError
from app.integrations.demo import DemoConnector
from app.models import IntegrationConnection, Project, SyncCheckpoint, SyncRun
from app.services.importer import import_batch
from app.services.risk import scan_project


def get_connector(db: Session, conn: IntegrationConnection) -> Connector:
    """Real connectors (GitHub/Jira/Slack/Calendar/Notion) register here in later stages."""
    if conn.is_demo:
        return DemoConnector(conn.provider, db, conn.workspace_id)
    raise ProviderError("invalid", f"No live connector is implemented for provider '{conn.provider}' in this build")


def run_sync(db: Session, conn: IntegrationConnection, project: Project, *, scan: bool = True) -> SyncRun:
    ckpt = db.scalar(select(SyncCheckpoint).where(SyncCheckpoint.connection_id == conn.id,
                                                  SyncCheckpoint.resource == "all"))
    mode = "incremental" if ckpt and ckpt.cursor else "initial"
    run = SyncRun(workspace_id=conn.workspace_id, connection_id=conn.id, mode=mode, status="running",
                  started_at=utcnow(), stats={})
    db.add(run)
    db.flush()
    try:
        connector = get_connector(db, conn)
        batch = connector.sync(mode, ckpt.cursor if ckpt else None)
        stats = import_batch(db, project, conn.provider, batch)
        run.stats = stats
        run.status = "partial" if batch.partial_errors else "succeeded"
        run.error = "; ".join(batch.partial_errors) or None
        if ckpt is None:
            ckpt = SyncCheckpoint(connection_id=conn.id, resource="all")
            db.add(ckpt)
        ckpt.cursor, ckpt.updated_at = batch.next_checkpoint, utcnow()
        conn.status, conn.last_success_at, conn.last_error = "connected", utcnow(), run.error
        if scan:
            scan_project(db, project)
    except ProviderError as exc:
        run.status, run.error = "failed", str(exc)
        conn.last_error = str(exc)
        if exc.kind in ("auth", "permission"):
            conn.status = "error"
    run.finished_at = utcnow()
    db.flush()
    return run
