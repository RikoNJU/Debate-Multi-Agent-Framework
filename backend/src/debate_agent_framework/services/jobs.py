"""接口联调使用的进程内任务状态存储。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class RunStageStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunStageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1)
    label: str = Field(min_length=1)
    status: RunStageStatus
    progress_percent: int = Field(ge=0, le=100)
    started_at: datetime
    completed_at: datetime | None = None
    detail: str | None = None


class RunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None
    error: str | None = None
    paper_id: str | None = None
    revision_id: str | None = None
    review_fingerprint: str | None = None
    current_stage: str | None = None
    current_stage_label: str | None = None
    progress_percent: int = Field(default=0, ge=0, le=100)
    stage_started_at: datetime | None = None
    stage_events: list[RunStageEvent] = Field(default_factory=list)


class InMemoryRunStore:
    """生产部署应替换为独立的持久化任务存储。"""

    def __init__(self) -> None:
        self._runs: dict[str, RunSnapshot] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        paper_id: str | None = None,
        revision_id: str | None = None,
        review_fingerprint: str | None = None,
    ) -> RunSnapshot:
        now = datetime.now(UTC)
        snapshot = RunSnapshot(
            task_id=uuid4().hex,
            status=RunStatus.QUEUED,
            created_at=now,
            updated_at=now,
            paper_id=paper_id,
            revision_id=revision_id,
            review_fingerprint=review_fingerprint,
            current_stage="queued",
            current_stage_label="等待开始",
            progress_percent=0,
        )
        with self._lock:
            self._runs[snapshot.task_id] = snapshot
        return snapshot.model_copy(deep=True)

    def mark_running(self, task_id: str) -> RunSnapshot:
        return self._update(
            task_id,
            status=RunStatus.RUNNING,
            result=None,
            error=None,
            current_stage="initializing",
            current_stage_label="正在初始化评审",
            progress_percent=1,
            stage_started_at=datetime.now(UTC),
        )

    def mark_resuming(self, task_id: str) -> RunSnapshot:
        """失败重试：恢复为运行中，但保留已完成步骤的进度记录。"""

        with self._lock:
            current = self._runs.get(task_id)
            if current is None:
                raise KeyError(task_id)
        completed = [
            event.progress_percent
            for event in current.stage_events
            if event.status is RunStageStatus.SUCCEEDED
        ]
        return self._update(
            task_id,
            status=RunStatus.RUNNING,
            result=None,
            error=None,
            current_stage="resuming",
            current_stage_label="正在从上次失败的步骤恢复",
            progress_percent=max(completed, default=0),
            stage_started_at=None,
        )

    def mark_stage(
        self,
        task_id: str,
        *,
        stage: str,
        label: str,
        status: RunStageStatus,
        progress_percent: int,
        detail: str | None = None,
    ) -> RunSnapshot:
        now = datetime.now(UTC)
        with self._lock:
            current = self._runs.get(task_id)
            if current is None:
                raise KeyError(task_id)
            events = list(current.stage_events)
            existing_index = next(
                (index for index, item in enumerate(events) if item.stage == stage),
                None,
            )
            started_at = (
                events[existing_index].started_at
                if existing_index is not None
                else now
            )
            event = RunStageEvent(
                stage=stage,
                label=label,
                status=status,
                progress_percent=progress_percent,
                started_at=started_at,
                completed_at=(now if status is not RunStageStatus.RUNNING else None),
                detail=detail,
            )
            if existing_index is None:
                events.append(event)
            else:
                events[existing_index] = event
            updated = current.model_copy(
                update={
                    "current_stage": stage,
                    "current_stage_label": label,
                    "progress_percent": progress_percent,
                    "stage_started_at": started_at,
                    "stage_events": events,
                    "updated_at": now,
                },
                deep=True,
            )
            self._runs[task_id] = updated
            return updated.model_copy(deep=True)

    def mark_succeeded(self, task_id: str, result: dict[str, Any]) -> RunSnapshot:
        return self._update(
            task_id,
            status=RunStatus.SUCCEEDED,
            result=result,
            error=None,
            current_stage="completed",
            current_stage_label="评审已完成",
            progress_percent=100,
            stage_started_at=None,
        )

    def mark_failed(self, task_id: str, error: str) -> RunSnapshot:
        return self._update(
            task_id,
            status=RunStatus.FAILED,
            result=None,
            error=error,
            current_stage="failed",
            current_stage_label="评审失败",
            stage_started_at=None,
        )

    def get(self, task_id: str) -> RunSnapshot | None:
        with self._lock:
            snapshot = self._runs.get(task_id)
            return snapshot.model_copy(deep=True) if snapshot else None

    def list_for_paper(self, paper_id: str) -> list[RunSnapshot]:
        with self._lock:
            return [
                snapshot.model_copy(deep=True)
                for snapshot in self._runs.values()
                if snapshot.paper_id == paper_id
            ]

    def find_succeeded_by_fingerprint(
        self, review_fingerprint: str
    ) -> RunSnapshot | None:
        with self._lock:
            matches = [
                snapshot
                for snapshot in self._runs.values()
                if snapshot.review_fingerprint == review_fingerprint
                and snapshot.status is RunStatus.SUCCEEDED
            ]
            if not matches:
                return None
            return max(matches, key=lambda item: item.updated_at).model_copy(deep=True)

    def _update(self, task_id: str, **changes: Any) -> RunSnapshot:
        with self._lock:
            current = self._runs.get(task_id)
            if current is None:
                raise KeyError(task_id)
            updated = current.model_copy(
                update={**changes, "updated_at": datetime.now(UTC)},
                deep=True,
            )
            self._runs[task_id] = updated
            return updated.model_copy(deep=True)
