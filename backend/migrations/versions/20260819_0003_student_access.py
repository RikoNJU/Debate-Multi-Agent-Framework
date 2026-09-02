"""Add anonymous student task access and review publication fields."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260819_0003"
down_revision: Union[str, Sequence[str], None] = "20260819_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
    with op.batch_alter_table("human_reviews") as batch_op:
        batch_op.add_column(sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("published_by_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_human_reviews_published_by_id_users",
            "users",
            ["published_by_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(op.f("ix_human_reviews_published_at"), ["published_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("human_reviews") as batch_op:
        batch_op.drop_index(op.f("ix_human_reviews_published_at"))
        batch_op.drop_constraint("fk_human_reviews_published_by_id_users", type_="foreignkey")
        batch_op.drop_column("published_by_id")
        batch_op.drop_column("published_at")
    op.drop_table("student_task_access")
