"""Persist model token, prompt-cache and cost metrics.

Revision ID: 20260828_0010
Revises: 20260828_0009
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260828_0010"
down_revision = "20260828_0009"
branch_labels = None
depends_on = None


_RUN_COLUMNS = (
    sa.Column("model_call_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column(
        "model_failed_call_count", sa.Integer(), nullable=False, server_default="0"
    ),
    sa.Column("model_prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("model_cache_hit_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("model_cache_miss_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("model_completion_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("model_reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column(
        "model_estimated_cost_yuan", sa.Float(), nullable=False, server_default="0"
    ),
)


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    existing = {item["name"] for item in inspector.get_columns("review_runs")}
    for column in _RUN_COLUMNS:
        if column.name not in existing:
            op.add_column("review_runs", column)
    if "model_call_metrics" not in tables:
        op.create_table(
            "model_call_metrics",
            sa.Column("call_id", sa.String(32), primary_key=True),
            sa.Column(
                "task_id",
                sa.String(32),
                sa.ForeignKey("review_runs.task_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("node", sa.String(64), nullable=True),
            sa.Column("role", sa.String(64), nullable=True),
            sa.Column("operation", sa.String(64), nullable=False),
            sa.Column("model", sa.String(128), nullable=False),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cache_hit_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cache_miss_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "estimated_cost_yuan", sa.Float(), nullable=False, server_default="0"
            ),
            sa.Column("prompt_prefix_hash", sa.String(64), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_model_call_metrics_task_id", "model_call_metrics", ["task_id"])
        op.create_index("ix_model_call_metrics_node", "model_call_metrics", ["node"])
        op.create_index(
            "ix_model_call_metrics_operation", "model_call_metrics", ["operation"]
        )
        op.create_index("ix_model_call_metrics_model", "model_call_metrics", ["model"])
        op.create_index("ix_model_call_metrics_status", "model_call_metrics", ["status"])
        op.create_index(
            "ix_model_call_metrics_prompt_prefix_hash",
            "model_call_metrics",
            ["prompt_prefix_hash"],
        )


def downgrade() -> None:
    op.drop_table("model_call_metrics")
    for column in reversed(_RUN_COLUMNS):
        op.drop_column("review_runs", column.name)
