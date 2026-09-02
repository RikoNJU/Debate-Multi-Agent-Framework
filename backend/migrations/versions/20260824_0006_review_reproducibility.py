"""Add revision comparison metadata and review fingerprints."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260824_0006"
down_revision: Union[str, Sequence[str], None] = "20260821_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    revision_columns = {
        column["name"] for column in inspector.get_columns("paper_revisions")
    }
    columns = (
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("parent_revision_id", sa.String(length=32), nullable=True),
        sa.Column("chapter_hashes_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("change_ratio", sa.Float(), nullable=True),
        sa.Column("change_summary_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    for column in columns:
        if column.name not in revision_columns:
            op.add_column("paper_revisions", column)

    revision_indexes = {
        index["name"] for index in inspect(bind).get_indexes("paper_revisions")
    }
    for index_name, column_name in (
        (op.f("ix_paper_revisions_content_sha256"), "content_sha256"),
        (op.f("ix_paper_revisions_parent_revision_id"), "parent_revision_id"),
    ):
        if index_name not in revision_indexes:
            op.create_index(index_name, "paper_revisions", [column_name], unique=False)
    # SQLite cannot add a self-referential foreign key without rebuilding the
    # table, which creates a circular column-order dependency in Alembic batch
    # mode. New SQLite databases still receive the FK from SQLAlchemy metadata.
    foreign_keys = {
        foreign_key["name"]
        for foreign_key in inspect(bind).get_foreign_keys("paper_revisions")
    }
    if bind.dialect.name != "sqlite" and "fk_paper_revisions_parent_revision_id" not in foreign_keys:
        op.create_foreign_key(
            "fk_paper_revisions_parent_revision_id",
            "paper_revisions",
            "paper_revisions",
            ["parent_revision_id"],
            ["id"],
            ondelete="SET NULL",
        )
    run_columns = {column["name"] for column in inspect(bind).get_columns("review_runs")}
    if "review_fingerprint" not in run_columns:
        op.add_column(
            "review_runs",
            sa.Column("review_fingerprint", sa.String(length=64), nullable=True),
        )
    run_indexes = {index["name"] for index in inspect(bind).get_indexes("review_runs")}
    fingerprint_index = op.f("ix_review_runs_review_fingerprint")
    if fingerprint_index not in run_indexes:
        op.create_index(
            fingerprint_index,
            "review_runs",
            ["review_fingerprint"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("review_runs") as batch_op:
        batch_op.drop_index(op.f("ix_review_runs_review_fingerprint"))
        batch_op.drop_column("review_fingerprint")
    with op.batch_alter_table("paper_revisions") as batch_op:
        batch_op.drop_index(op.f("ix_paper_revisions_parent_revision_id"))
        batch_op.drop_index(op.f("ix_paper_revisions_content_sha256"))
        if op.get_bind().dialect.name != "sqlite":
            batch_op.drop_constraint("fk_paper_revisions_parent_revision_id", type_="foreignkey")
        batch_op.drop_column("change_summary_json")
        batch_op.drop_column("change_ratio")
        batch_op.drop_column("chapter_hashes_json")
        batch_op.drop_column("parent_revision_id")
        batch_op.drop_column("content_sha256")
