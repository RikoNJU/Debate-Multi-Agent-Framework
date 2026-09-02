"""Add authentication, assignments, human reviews, and audit logs."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260819_0002"
down_revision: Union[str, Sequence[str], None] = "20260819_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    op.create_index(op.f("ix_users_is_active"), "users", ["is_active"], unique=False)

    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_auth_sessions_expires_at"), "auth_sessions", ["expires_at"], unique=False)

    op.create_table(
        "paper_assignments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("paper_id", sa.String(length=255), nullable=False),
        sa.Column("reviewer_id", sa.String(length=32), nullable=False),
        sa.Column("assigned_by_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("paper_id", "reviewer_id", name="uq_assignment_paper_reviewer"),
    )
    op.create_index(op.f("ix_paper_assignments_paper_id"), "paper_assignments", ["paper_id"], unique=False)
    op.create_index(op.f("ix_paper_assignments_reviewer_id"), "paper_assignments", ["reviewer_id"], unique=False)
    op.create_index(op.f("ix_paper_assignments_assigned_by_id"), "paper_assignments", ["assigned_by_id"], unique=False)
    op.create_index(op.f("ix_paper_assignments_status"), "paper_assignments", ["status"], unique=False)

    op.create_table(
        "human_reviews",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("assignment_id", sa.String(length=32), nullable=False),
        sa.Column("paper_id", sa.String(length=255), nullable=False),
        sa.Column("reviewer_id", sa.String(length=32), nullable=False),
        sa.Column("ai_task_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("section_scores", sa.JSON(), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("advice_content", sa.Text(), nullable=False),
        sa.Column("teacher_comments", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assignment_id"], ["paper_assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ai_task_id"], ["review_runs.task_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id"),
    )
    op.create_index(op.f("ix_human_reviews_assignment_id"), "human_reviews", ["assignment_id"], unique=True)
    op.create_index(op.f("ix_human_reviews_paper_id"), "human_reviews", ["paper_id"], unique=False)
    op.create_index(op.f("ix_human_reviews_reviewer_id"), "human_reviews", ["reviewer_id"], unique=False)
    op.create_index(op.f("ix_human_reviews_status"), "human_reviews", ["status"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_actor_id"), "audit_logs", ["actor_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_resource_type"), "audit_logs", ["resource_type"], unique=False)
    op.create_index(op.f("ix_audit_logs_resource_id"), "audit_logs", ["resource_id"], unique=False)


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("human_reviews")
    op.drop_table("paper_assignments")
    op.drop_table("auth_sessions")
    op.drop_table("users")
