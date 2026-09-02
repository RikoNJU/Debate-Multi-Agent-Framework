"""Drop the student task access table now that the student portal is open."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260821_0005"
down_revision: Union[str, Sequence[str], None] = "20260820_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("student_task_access")


def downgrade() -> None:
    op.create_table(
        "student_task_access",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("paper_id", sa.String(length=255), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["review_runs.task_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_student_task_access_task_id"), "student_task_access", ["task_id"], unique=True)
    op.create_index(op.f("ix_student_task_access_paper_id"), "student_task_access", ["paper_id"], unique=False)
    op.create_index(op.f("ix_student_task_access_token_hash"), "student_task_access", ["token_hash"], unique=True)
