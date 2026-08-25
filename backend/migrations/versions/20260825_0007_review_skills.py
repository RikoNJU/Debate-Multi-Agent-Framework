"""Persist discipline Skill selection for review auditability."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "20260825_0007"
down_revision: Union[str, Sequence[str], None] = "20260824_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("review_runs")
    }
    if "discipline_id" not in existing:
        op.add_column(
            "review_runs", sa.Column("discipline_id", sa.String(64), nullable=True)
        )
    if "skill_selection_hash" not in existing:
        op.add_column(
            "review_runs",
            sa.Column("skill_selection_hash", sa.String(64), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("review_runs") as batch_op:
        batch_op.drop_column("skill_selection_hash")
        batch_op.drop_column("discipline_id")
