from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

TS = DateTime(timezone=True)


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"
    __table_args__ = (UniqueConstraint("workspace_id", "provider", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30))  # jira|github|slack|google_calendar|notion
    name: Mapped[str] = mapped_column(String(100), default="default")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="connected")  # connected|error|disconnected
    # Fernet token. Never serialised to clients, logs or audit payloads.
    encrypted_credentials: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    capabilities: Mapped[list] = mapped_column(JSONB, default=list)
    last_success_at: Mapped[datetime | None] = mapped_column(TS)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(20))  # initial|incremental
    status: Mapped[str] = mapped_column(String(20))  # running|succeeded|partial|failed
    started_at: Mapped[datetime] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class SyncCheckpoint(Base):
    __tablename__ = "sync_checkpoints"
    __table_args__ = (UniqueConstraint("connection_id", "resource"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("integration_connections.id", ondelete="CASCADE"))
    resource: Mapped[str] = mapped_column(String(100))
    cursor: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Evidence(Base):
    """Provider-sourced fact that findings, answers and summaries may cite. Authoritative; AI never writes here."""
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", "kind", "external_id"),
        Index("ix_evidence_project", "project_id", "provider_timestamp"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(30))  # issue|pull_request|comment|message|document|check
    external_id: Mapped[str] = mapped_column(String(200))
    ref_key: Mapped[str | None] = mapped_column(String(50))  # issue key this relates to, if any
    url: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(500))
    excerpt: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str | None] = mapped_column(String(200))
    provider_timestamp: Mapped[datetime] = mapped_column(TS)
    retrieved_at: Mapped[datetime] = mapped_column(TS)
