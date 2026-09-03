"""Add standalone AIGC detection tasks.

Revision ID: 20260903_0011
Revises: 20260828_0010
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260903_0011"
down_revision = "20260828_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "aigc_detection_tasks" not in tables:
        op.create_table(
            "aigc_detection_tasks",
            sa.Column("task_id", sa.String(32), primary_key=True),
            sa.Column("access_code_hash", sa.String(64), nullable=False),
            sa.Column("source_filename", sa.Text(), nullable=False),
            sa.Column("source_pdf_path", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("current_stage", sa.String(64), nullable=False),
            sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("model_id", sa.String(255), nullable=False),
            sa.Column("model_revision", sa.String(128), nullable=True),
            sa.Column("preprocessing_version", sa.String(64), nullable=False),
            sa.Column("result_json", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_aigc_detection_tasks_status", "aigc_detection_tasks", ["status"])
    if "aigc_segment_results" not in tables:
        op.create_table(
            "aigc_segment_results",
            sa.Column(
                "task_id",
                sa.String(32),
                sa.ForeignKey("aigc_detection_tasks.task_id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("segment_id", sa.String(32), primary_key=True),
            sa.Column("chapter_id", sa.String(64), nullable=True),
            sa.Column("text_sha256", sa.String(64), nullable=False),
            sa.Column("token_count", sa.Integer(), nullable=False),
            sa.Column("ai_probability", sa.Float(), nullable=False),
            sa.Column("risk_level", sa.String(16), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
        )
        op.create_index("ix_aigc_segment_results_chapter_id", "aigc_segment_results", ["chapter_id"])
        op.create_index("ix_aigc_segment_results_text_sha256", "aigc_segment_results", ["text_sha256"])
        op.create_index("ix_aigc_segment_results_risk_level", "aigc_segment_results", ["risk_level"])


def downgrade() -> None:
    op.drop_table("aigc_segment_results")
    op.drop_table("aigc_detection_tasks")
