"""Persist the resolved review Skill audit snapshot.

Revision ID: 20260825_0008
Revises: 20260825_0007
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260825_0008"
down_revision = "20260825_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("review_runs")
    }
    columns = (
        sa.Column("skill_id", sa.String(128), nullable=True),
        sa.Column("skill_version", sa.String(64), nullable=True),
        sa.Column("skill_profile_hash", sa.String(64), nullable=True),
        sa.Column(
            "skill_versions_json", sa.JSON(), nullable=False, server_default="{}"
        ),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("review_runs", column)


def downgrade() -> None:
    op.drop_column("review_runs", "skill_versions_json")
    op.drop_column("review_runs", "skill_profile_hash")
    op.drop_column("review_runs", "skill_version")
    op.drop_column("review_runs", "skill_id")
