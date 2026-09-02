"""Persist node-level review progress."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260820_0004"
down_revision: Union[str, Sequence[str], None] = "20260819_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_run_stages",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"], ["review_runs.task_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "stage", name="uq_review_run_stage"),
    )
    op.create_index(
        op.f("ix_review_run_stages_task_id"),
        "review_run_stages",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_review_run_stages_status"),
        "review_run_stages",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("review_run_stages")
