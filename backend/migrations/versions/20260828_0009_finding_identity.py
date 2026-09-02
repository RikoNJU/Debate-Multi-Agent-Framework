"""Persist server-owned Finding identities and lineage.

Revision ID: 20260828_0009
Revises: 20260825_0008
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260828_0009"
down_revision = "20260825_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    review_run_columns = {
        column["name"] for column in inspector.get_columns("review_runs")
    }
    if "finding_identity_version" not in review_run_columns:
        op.add_column(
            "review_runs",
            sa.Column("finding_identity_version", sa.String(32), nullable=True),
        )
    if "source_findings" not in tables:
        _create_source_findings()
    if "canonical_findings" not in tables:
        _create_canonical_findings()
    if "canonical_finding_members" not in tables:
        _create_members()


def _create_source_findings() -> None:
    op.create_table(
        "source_findings",
        sa.Column("finding_id", sa.String(64), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(32),
            sa.ForeignKey("review_runs.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_role", sa.String(64), nullable=False),
        sa.Column("local_ref", sa.String(255), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "task_id", "source_role", "local_ref", name="uq_source_finding_local_ref"
        ),
    )
    op.create_index("ix_source_findings_task_id", "source_findings", ["task_id"])
    op.create_index(
        "ix_source_findings_source_role", "source_findings", ["source_role"]
    )
    op.create_index(
        "ix_source_findings_fingerprint", "source_findings", ["fingerprint"]
    )


def _create_canonical_findings() -> None:
    op.create_table(
        "canonical_findings",
        sa.Column("finding_id", sa.String(64), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(32),
            sa.ForeignKey("review_runs.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_canonical_findings_task_id", "canonical_findings", ["task_id"]
    )
    op.create_index(
        "ix_canonical_findings_status", "canonical_findings", ["status"]
    )
    op.create_index(
        "ix_canonical_findings_fingerprint", "canonical_findings", ["fingerprint"]
    )


def _create_members() -> None:
    op.create_table(
        "canonical_finding_members",
        sa.Column(
            "canonical_finding_id",
            sa.String(64),
            sa.ForeignKey("canonical_findings.finding_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_finding_id",
            sa.String(64),
            sa.ForeignKey("source_findings.finding_id", ondelete="CASCADE"),
            primary_key=True,
            unique=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("canonical_finding_members")
    op.drop_index("ix_canonical_findings_fingerprint", "canonical_findings")
    op.drop_index("ix_canonical_findings_status", "canonical_findings")
    op.drop_index("ix_canonical_findings_task_id", "canonical_findings")
    op.drop_table("canonical_findings")
    op.drop_index("ix_source_findings_fingerprint", "source_findings")
    op.drop_index("ix_source_findings_source_role", "source_findings")
    op.drop_index("ix_source_findings_task_id", "source_findings")
    op.drop_table("source_findings")
    op.drop_column("review_runs", "finding_identity_version")
