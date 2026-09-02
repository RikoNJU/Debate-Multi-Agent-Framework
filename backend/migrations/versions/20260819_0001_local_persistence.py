"""Create local paper persistence tables."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260819_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("paper_type", sa.String(length=32), nullable=True),
        sa.Column("source_filename", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("current_revision_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_papers_sha256"), "papers", ["sha256"], unique=False)
    op.create_table(
        "paper_revisions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("paper_id", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("pdf_path", sa.Text(), nullable=False),
        sa.Column("structured_input_path", sa.Text(), nullable=False),
        sa.Column("mineru_batch_id", sa.String(length=255), nullable=False),
        sa.Column("parse_status", sa.String(length=32), nullable=False),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_paper_revisions_paper_id"),
        "paper_revisions",
        ["paper_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_paper_revisions_sha256"),
        "paper_revisions",
        ["sha256"],
        unique=False,
    )
    op.create_table(
        "paper_artifacts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("revision_id", sa.String(length=32), nullable=False),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["paper_revisions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_paper_artifacts_revision_id"),
        "paper_artifacts",
        ["revision_id"],
        unique=False,
    )
    op.create_table(
        "review_runs",
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("paper_id", sa.String(length=255), nullable=True),
        sa.Column("revision_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_stage", sa.String(length=64), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index(op.f("ix_review_runs_paper_id"), "review_runs", ["paper_id"], unique=False)
    op.create_index(op.f("ix_review_runs_revision_id"), "review_runs", ["revision_id"], unique=False)
    op.create_index(op.f("ix_review_runs_status"), "review_runs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_review_runs_status"), table_name="review_runs")
    op.drop_index(op.f("ix_review_runs_revision_id"), table_name="review_runs")
    op.drop_index(op.f("ix_review_runs_paper_id"), table_name="review_runs")
    op.drop_table("review_runs")
    op.drop_index(op.f("ix_paper_artifacts_revision_id"), table_name="paper_artifacts")
    op.drop_table("paper_artifacts")
    op.drop_index(op.f("ix_paper_revisions_sha256"), table_name="paper_revisions")
    op.drop_index(op.f("ix_paper_revisions_paper_id"), table_name="paper_revisions")
    op.drop_table("paper_revisions")
    op.drop_index(op.f("ix_papers_sha256"), table_name="papers")
    op.drop_table("papers")
