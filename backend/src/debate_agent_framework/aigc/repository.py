from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from sqlalchemy import delete, update

from ..persistence.database import Database
from ..persistence.models import AigcDetectionTaskRecord, AigcSegmentResultRecord
from .schemas import AigcDetectionResult, AigcTaskSnapshot, AigcTaskStatus


class AigcTaskRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def hash_access_code(access_code: str) -> str:
        return hashlib.sha256(f"aigc-task:{access_code}".encode("utf-8")).hexdigest()

    def create(
        self,
        *,
        task_id: str,
        access_code: str,
        source_filename: str,
        source_pdf_path: str,
        model_id: str,
        model_revision: str | None,
        preprocessing_version: str,
    ) -> AigcTaskSnapshot:
        now = datetime.now(UTC)
        with self.database.session() as session:
            record = AigcDetectionTaskRecord(
                task_id=task_id,
                access_code_hash=self.hash_access_code(access_code),
                source_filename=source_filename,
                source_pdf_path=source_pdf_path,
                status=AigcTaskStatus.QUEUED.value,
                current_stage="queued",
                progress_percent=0,
                model_id=model_id,
                model_revision=model_revision,
                preprocessing_version=preprocessing_version,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            return self._snapshot(record)

    def get(self, task_id: str) -> AigcTaskSnapshot | None:
        with self.database.session() as session:
            record = session.get(AigcDetectionTaskRecord, task_id)
            return self._snapshot(record) if record else None

    def source_path(self, task_id: str) -> str:
        with self.database.session() as session:
            record = session.get(AigcDetectionTaskRecord, task_id)
            if record is None:
                raise KeyError(task_id)
            return record.source_pdf_path

    def authorize(self, task_id: str, access_code: str) -> bool:
        with self.database.session() as session:
            record = session.get(AigcDetectionTaskRecord, task_id)
            return bool(
                record
                and hmac.compare_digest(
                    record.access_code_hash, self.hash_access_code(access_code)
                )
            )

    def mark_stage(
        self, task_id: str, status: AigcTaskStatus, stage: str, progress: int
    ) -> None:
        with self.database.session() as session:
            changed = session.execute(
                update(AigcDetectionTaskRecord)
                .where(AigcDetectionTaskRecord.task_id == task_id)
                .values(
                    status=status.value,
                    current_stage=stage,
                    progress_percent=progress,
                    error=None,
                    updated_at=datetime.now(UTC),
                )
            ).rowcount
            if not changed:
                raise KeyError(task_id)

    def mark_succeeded(
        self, task_id: str, result: AigcDetectionResult
    ) -> AigcTaskSnapshot:
        now = datetime.now(UTC)
        with self.database.session() as session:
            record = session.get(AigcDetectionTaskRecord, task_id)
            if record is None:
                raise KeyError(task_id)
            session.execute(
                delete(AigcSegmentResultRecord).where(
                    AigcSegmentResultRecord.task_id == task_id
                )
            )
            for item in result.segments:
                payload = item.model_dump(mode="json")
                session.add(
                    AigcSegmentResultRecord(
                        task_id=task_id,
                        segment_id=item.segment_id,
                        chapter_id=item.chapter_id,
                        text_sha256=item.text_sha256,
                        token_count=item.token_count,
                        ai_probability=item.ai_probability,
                        risk_level=item.risk_level.value,
                        payload_json=payload,
                    )
                )
            record.status = AigcTaskStatus.SUCCEEDED.value
            record.current_stage = "completed"
            record.progress_percent = 100
            record.result_json = result.model_dump(mode="json")
            record.error = None
            record.updated_at = now
            record.completed_at = now
            session.flush()
            return self._snapshot(record)

    def mark_failed(self, task_id: str, error: str) -> None:
        with self.database.session() as session:
            session.execute(
                update(AigcDetectionTaskRecord)
                .where(AigcDetectionTaskRecord.task_id == task_id)
                .values(
                    status=AigcTaskStatus.FAILED.value,
                    current_stage="failed",
                    error=error[:1000],
                    updated_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                )
            )

    def prepare_retry(self, task_id: str) -> AigcTaskSnapshot:
        with self.database.session() as session:
            record = session.get(AigcDetectionTaskRecord, task_id)
            if record is None:
                raise KeyError(task_id)
            if record.status not in {
                AigcTaskStatus.FAILED.value,
                AigcTaskStatus.INTERRUPTED.value,
            }:
                raise ValueError("Only failed or interrupted AIGC tasks can be retried")
            record.status = AigcTaskStatus.QUEUED.value
            record.current_stage = "queued"
            record.progress_percent = 0
            record.error = None
            record.result_json = None
            record.completed_at = None
            record.updated_at = datetime.now(UTC)
            session.execute(
                delete(AigcSegmentResultRecord).where(
                    AigcSegmentResultRecord.task_id == task_id
                )
            )
            session.flush()
            return self._snapshot(record)

    def delete(self, task_id: str) -> None:
        with self.database.session() as session:
            record = session.get(AigcDetectionTaskRecord, task_id)
            if record is None:
                raise KeyError(task_id)
            session.execute(
                delete(AigcSegmentResultRecord).where(
                    AigcSegmentResultRecord.task_id == task_id
                )
            )
            session.delete(record)

    def mark_interrupted(self) -> int:
        with self.database.session() as session:
            result = session.execute(
                update(AigcDetectionTaskRecord)
                .where(
                    AigcDetectionTaskRecord.status.in_(
                        [
                            AigcTaskStatus.PARSING.value,
                            AigcTaskStatus.DETECTING.value,
                        ]
                    )
                )
                .values(
                    status=AigcTaskStatus.INTERRUPTED.value,
                    current_stage="interrupted",
                    error="Service restart interrupted AIGC detection; retry the task.",
                    updated_at=datetime.now(UTC),
                )
            )
            return int(result.rowcount or 0)

    @staticmethod
    def _snapshot(record: AigcDetectionTaskRecord) -> AigcTaskSnapshot:
        return AigcTaskSnapshot(
            task_id=record.task_id,
            source_filename=record.source_filename,
            status=AigcTaskStatus(record.status),
            current_stage=record.current_stage,
            progress_percent=record.progress_percent,
            result=(
                AigcDetectionResult.model_validate(record.result_json)
                if record.result_json
                else None
            ),
            error=record.error,
            created_at=record.created_at,
            updated_at=record.updated_at,
            completed_at=record.completed_at,
        )
