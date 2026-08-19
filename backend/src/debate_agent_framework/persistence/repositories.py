"""Repositories backed by SQLAlchemy sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from ..schemas import DebateReviewInput
from ..services.jobs import RunSnapshot, RunStatus
from .database import Database
from .models import (
    PaperArtifactRecord,
    PaperRecord,
    PaperRevisionRecord,
    ReviewRunRecord,
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SqlAlchemyRunStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        paper_id: str | None = None,
        revision_id: str | None = None,
    ) -> RunSnapshot:
        now = datetime.now(UTC)
        record = ReviewRunRecord(
            task_id=uuid4().hex,
            paper_id=paper_id,
            revision_id=revision_id,
            status=RunStatus.QUEUED.value,
            current_stage="queued",
            created_at=now,
            updated_at=now,
        )
        with self.database.session() as session:
            session.add(record)
        return self._snapshot(record)

    def mark_running(self, task_id: str) -> RunSnapshot:
        return self._update(
            task_id,
            status=RunStatus.RUNNING,
            current_stage="workflow",
            result_json=None,
            error=None,
        )

    def mark_succeeded(self, task_id: str, result: dict[str, Any]) -> RunSnapshot:
        return self._update(
            task_id,
            status=RunStatus.SUCCEEDED,
            current_stage="completed",
            result_json=result,
            error=None,
        )

    def mark_failed(self, task_id: str, error: str) -> RunSnapshot:
        return self._update(
            task_id,
            status=RunStatus.FAILED,
            current_stage="failed",
            result_json=None,
            error=error,
        )

    def get(self, task_id: str) -> RunSnapshot | None:
        with self.database.session() as session:
            record = session.get(ReviewRunRecord, task_id)
            return self._snapshot(record) if record else None

    def list_for_paper(self, paper_id: str) -> list[RunSnapshot]:
        with self.database.session() as session:
            records = session.scalars(
                select(ReviewRunRecord)
                .where(ReviewRunRecord.paper_id == paper_id)
                .order_by(ReviewRunRecord.created_at.desc())
            ).all()
            return [self._snapshot(record) for record in records]

    def mark_interrupted(self) -> int:
        changed = 0
        with self.database.session() as session:
            records = session.scalars(
                select(ReviewRunRecord).where(
                    ReviewRunRecord.status == RunStatus.RUNNING.value
                )
            ).all()
            for record in records:
                record.status = RunStatus.INTERRUPTED.value
                record.current_stage = "interrupted"
                record.error = "服务重启导致评审中断，请重新提交评审任务。"
                record.updated_at = datetime.now(UTC)
                changed += 1
        return changed

    def _update(
        self,
        task_id: str,
        *,
        status: RunStatus,
        current_stage: str,
        result_json: dict[str, Any] | None,
        error: str | None,
    ) -> RunSnapshot:
        with self.database.session() as session:
            record = session.get(ReviewRunRecord, task_id)
            if record is None:
                raise KeyError(task_id)
            record.status = status.value
            record.current_stage = current_stage
            record.result_json = result_json
            record.error = error
            record.updated_at = datetime.now(UTC)
            session.flush()
            return self._snapshot(record)

    @staticmethod
    def _snapshot(record: ReviewRunRecord) -> RunSnapshot:
        return RunSnapshot(
            task_id=record.task_id,
            status=RunStatus(record.status),
            created_at=_aware(record.created_at),
            updated_at=_aware(record.updated_at),
            result=record.result_json,
            error=record.error,
            paper_id=record.paper_id,
            revision_id=record.revision_id,
            current_stage=record.current_stage,
        )


class PaperRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_revision(
        self,
        *,
        review_input: DebateReviewInput,
        source_filename: str,
        revision_id: str,
        pdf_sha256: str,
        pdf_path: str,
        structured_input_path: str,
        mineru_batch_id: str,
        artifacts: list[dict[str, Any]],
    ) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            paper = session.get(PaperRecord, review_input.paper_id)
            if paper is None:
                paper = PaperRecord(
                    id=review_input.paper_id,
                    title=review_input.title,
                    paper_type=(
                        review_input.paper_type.value if review_input.paper_type else None
                    ),
                    source_filename=source_filename,
                    sha256=pdf_sha256,
                    current_revision_id=revision_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(paper)
            else:
                paper.title = review_input.title
                paper.paper_type = (
                    review_input.paper_type.value if review_input.paper_type else None
                )
                paper.source_filename = source_filename
                paper.sha256 = pdf_sha256
                paper.current_revision_id = revision_id
                paper.updated_at = now

            session.add(
                PaperRevisionRecord(
                    id=revision_id,
                    paper_id=review_input.paper_id,
                    sha256=pdf_sha256,
                    pdf_path=pdf_path,
                    structured_input_path=structured_input_path,
                    mineru_batch_id=mineru_batch_id,
                    parse_status="succeeded",
                    created_at=now,
                )
            )
            session.add_all(
                PaperArtifactRecord(
                    id=uuid4().hex,
                    revision_id=revision_id,
                    artifact_type=item["artifact_type"],
                    relative_path=item["relative_path"],
                    file_size=item["file_size"],
                    sha256=item["sha256"],
                    metadata_json=item.get("metadata", {}),
                )
                for item in artifacts
            )

    def get_paper(self, paper_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            paper = session.get(PaperRecord, paper_id)
            if paper is None:
                return None
            revisions = session.scalars(
                select(PaperRevisionRecord)
                .where(PaperRevisionRecord.paper_id == paper_id)
                .order_by(PaperRevisionRecord.created_at.desc())
            ).all()
            return {
                "paper_id": paper.id,
                "title": paper.title,
                "paper_type": paper.paper_type,
                "source_filename": paper.source_filename,
                "sha256": paper.sha256,
                "current_revision_id": paper.current_revision_id,
                "created_at": _aware(paper.created_at),
                "updated_at": _aware(paper.updated_at),
                "revisions": [
                    {
                        "revision_id": revision.id,
                        "sha256": revision.sha256,
                        "mineru_batch_id": revision.mineru_batch_id,
                        "parse_status": revision.parse_status,
                        "created_at": _aware(revision.created_at),
                        "artifact_count": len(revision.artifacts),
                    }
                    for revision in revisions
                ],
            }
