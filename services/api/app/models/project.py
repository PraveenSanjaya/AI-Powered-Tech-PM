from datetime import date, datetime

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

TS = DateTime(timezone=True)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Sprint(Base):
    __tablename__ = "sprints"
    __table_args__ = (UniqueConstraint("project_id", "provider", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30), default="local")
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text, default="")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(20), default="active")  # planned|active|closed
    url: Mapped[str | None] = mapped_column(String(500))
    last_synced_at: Mapped[datetime | None] = mapped_column(TS)


class ExternalIdentity(Base):
    """A person as seen by one provider. Linked to a local user only by explicit confirmation."""
    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("workspace_id", "provider", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="unresolved")  # unresolved|confirmed
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(TS)


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", "external_id"),
        Index("ix_issues_project_sprint", "project_id", "sprint_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    sprint_id: Mapped[int | None] = mapped_column(ForeignKey("sprints.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(60))
    status_category: Mapped[str] = mapped_column(String(20))  # todo|in_progress|done
    priority: Mapped[str | None] = mapped_column(String(30))
    assignee_identity_id: Mapped[int | None] = mapped_column(ForeignKey("external_identities.id", ondelete="SET NULL"))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    labels: Mapped[list] = mapped_column(JSONB, default=list)
    story_points: Mapped[int | None] = mapped_column(Integer)
    due_date: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str] = mapped_column(String(500))
    provider_created_at: Mapped[datetime] = mapped_column(TS)
    provider_updated_at: Mapped[datetime] = mapped_column(TS)
    last_activity_at: Mapped[datetime] = mapped_column(TS)
    last_synced_at: Mapped[datetime] = mapped_column(TS)


class IssueLink(Base):
    __tablename__ = "issue_links"
    __table_args__ = (UniqueConstraint("issue_id", "target_key", "link_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    target_key: Mapped[str] = mapped_column(String(50))
    link_type: Mapped[str] = mapped_column(String(30))  # blocked_by | relates


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("workspace_id", "provider", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(100))
    repo: Mapped[str] = mapped_column(String(200))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    author_identity_id: Mapped[int | None] = mapped_column(ForeignKey("external_identities.id", ondelete="SET NULL"))
    state: Mapped[str] = mapped_column(String(20))  # open|merged|closed
    draft: Mapped[bool] = mapped_column(Boolean, default=False)
    review_state: Mapped[str] = mapped_column(String(30), default="awaiting_review")
    checks_state: Mapped[str] = mapped_column(String(20), default="none")  # passing|failing|pending|none
    issue_key: Mapped[str | None] = mapped_column(String(50))
    url: Mapped[str] = mapped_column(String(500))
    provider_created_at: Mapped[datetime] = mapped_column(TS)
    provider_updated_at: Mapped[datetime] = mapped_column(TS)
    review_requested_at: Mapped[datetime | None] = mapped_column(TS)
    last_synced_at: Mapped[datetime] = mapped_column(TS)


class PullRequestReview(Base):
    __tablename__ = "pull_request_reviews"
    __table_args__ = (UniqueConstraint("pull_request_id", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(100))
    reviewer_identity_id: Mapped[int | None] = mapped_column(ForeignKey("external_identities.id", ondelete="SET NULL"))
    state: Mapped[str] = mapped_column(String(30))  # approved|changes_requested|commented
    submitted_at: Mapped[datetime] = mapped_column(TS)


class PullRequestCheck(Base):
    __tablename__ = "pull_request_checks"
    __table_args__ = (UniqueConstraint("pull_request_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))  # queued|in_progress|completed
    conclusion: Mapped[str | None] = mapped_column(String(20))  # success|failure|...
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str | None] = mapped_column(String(500))
    completed_at: Mapped[datetime | None] = mapped_column(TS)
