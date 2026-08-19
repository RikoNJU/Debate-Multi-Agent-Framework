"""Relational models for local paper and review persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class PaperRecord(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    paper_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    current_revision_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    revisions: Mapped[list[PaperRevisionRecord]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )


class PaperRevisionRecord(Base):
    __tablename__ = "paper_revisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    pdf_path: Mapped[str] = mapped_column(Text)
    structured_input_path: Mapped[str] = mapped_column(Text)
    mineru_batch_id: Mapped[str] = mapped_column(String(255))
    parse_status: Mapped[str] = mapped_column(String(32), default="succeeded")
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    paper: Mapped[PaperRecord] = relationship(back_populates="revisions")
    artifacts: Mapped[list[PaperArtifactRecord]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )


class PaperArtifactRecord(Base):
    __tablename__ = "paper_artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("paper_revisions.id", ondelete="CASCADE"), index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(64))
    relative_path: Mapped[str] = mapped_column(Text)
    file_size: Mapped[int] = mapped_column()
    sha256: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    revision: Mapped[PaperRevisionRecord] = relationship(back_populates="artifacts")


class ReviewRunRecord(Base):
    __tablename__ = "review_runs"

    task_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    paper_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    revision_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StudentTaskAccessRecord(Base):
    __tablename__ = "student_task_access"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("review_runs.task_id", ondelete="CASCADE"), unique=True, index=True
    )
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuthSessionRecord(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PaperAssignmentRecord(Base):
    __tablename__ = "paper_assignments"
    __table_args__ = (
        UniqueConstraint("paper_id", "reviewer_id", name="uq_assignment_paper_reviewer"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    assigned_by_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="assigned", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HumanReviewRecord(Base):
    __tablename__ = "human_reviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("paper_assignments.id", ondelete="CASCADE"), unique=True, index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ai_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_runs.task_id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    section_scores: Mapped[list[int]] = mapped_column(JSON, default=list)
    total_score: Mapped[int] = mapped_column()
    advice_content: Mapped[str] = mapped_column(Text, default="")
    teacher_comments: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    published_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AuditLogRecord(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    actor_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    resource_id: Mapped[str] = mapped_column(String(255), index=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
