"""Debate 论文评审任务的应用服务。"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from debate_agent_framework.schemas import DebateReviewInput
from debate_agent_framework.services.jobs import (
    InMemoryRunStore,
    RunSnapshot,
    RunStageStatus,
)
from debate_agent_framework.workflows import DebateWorkflow, build_workflow


class RunStore(Protocol):
    def create(
        self,
        *,
        paper_id: str | None = None,
        revision_id: str | None = None,
        review_fingerprint: str | None = None,
        discipline_id: str | None = None,
        skill_selection_hash: str | None = None,
    ) -> RunSnapshot: ...

    def mark_running(self, task_id: str) -> RunSnapshot: ...

    def mark_resuming(self, task_id: str) -> RunSnapshot: ...

    def mark_stage(
        self,
        task_id: str,
        *,
        stage: str,
        label: str,
        status: RunStageStatus,
        progress_percent: int,
        detail: str | None = None,
    ) -> RunSnapshot: ...

    def mark_succeeded(self, task_id: str, result: dict) -> RunSnapshot: ...

    def record_model_call(self, task_id: str, metric: dict[str, Any]) -> None: ...

    def mark_failed(self, task_id: str, error: str) -> RunSnapshot: ...

    def get(self, task_id: str) -> RunSnapshot | None: ...

    def list_for_paper(self, paper_id: str) -> list[RunSnapshot]: ...

    def find_succeeded_by_fingerprint(
        self, review_fingerprint: str
    ) -> RunSnapshot | None: ...


class DebateWorkflowService:
    def __init__(
        self,
        workflow: DebateWorkflow | None = None,
        store: RunStore | None = None,
        *,
        runtime: str | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self.workflow = workflow or build_workflow(
            runtime or "demo", checkpointer=checkpointer
        )
        self.store = store or InMemoryRunStore()

    def create_run(
        self,
        *,
        paper_id: str | None = None,
        revision_id: str | None = None,
        review_fingerprint: str | None = None,
        discipline_id: str | None = None,
        skill_selection_hash: str | None = None,
    ) -> RunSnapshot:
        return self.store.create(
            paper_id=paper_id,
            revision_id=revision_id,
            review_fingerprint=review_fingerprint,
            discipline_id=discipline_id,
            skill_selection_hash=skill_selection_hash,
        )

    def find_reusable_run(self, review_fingerprint: str) -> RunSnapshot | None:
        return self.store.find_succeeded_by_fingerprint(review_fingerprint)

    def skill_selection_hash(
        self, discipline_id: str, paper_type: object | None
    ) -> str:
        resolver = self.workflow.services.skill_resolver
        if resolver is None:
            raise ValueError("Review Skill Resolver is not configured")
        return resolver.selection_hash(discipline_id, paper_type)

    def create_reused_run(
        self,
        *,
        source: RunSnapshot,
        paper_id: str,
        revision_id: str,
        review_fingerprint: str,
        discipline_id: str = "artificial_intelligence",
        skill_selection_hash: str | None = None,
    ) -> RunSnapshot:
        if source.result is None:
            raise ValueError("可复用评审缺少结果")
        created = self.create_run(
            paper_id=paper_id,
            revision_id=revision_id,
            review_fingerprint=review_fingerprint,
            discipline_id=discipline_id,
            skill_selection_hash=skill_selection_hash,
        )
        return self.store.mark_succeeded(created.task_id, source.result)

    async def execute(self, task_id: str, review_input: DebateReviewInput) -> None:
        self.store.mark_running(task_id)

        async def record_progress(
            stage: str,
            label: str,
            stage_status: str,
            progress_percent: int,
            detail: str | None,
        ) -> None:
            await asyncio.to_thread(
                self.store.mark_stage,
                task_id,
                stage=stage,
                label=label,
                status=RunStageStatus(stage_status),
                progress_percent=progress_percent,
                detail=detail,
            )

        try:
            def record_model_call(metric: Any) -> None:
                self.store.record_model_call(task_id, metric.as_dict())

            result = await self.workflow.arun(
                review_input,
                progress_callback=record_progress,
                thread_id=task_id,
                model_call_observer=record_model_call,
            )
            self.store.mark_succeeded(task_id, result.model_dump(mode="json"))
        except Exception as exc:
            self.store.mark_failed(task_id, str(exc))

    def prepare_resume(self, task_id: str) -> RunSnapshot:
        """失败重试前把任务恢复为运行中，保留已完成步骤的进度。"""

        return self.store.mark_resuming(task_id)

    async def resume_run(self, task_id: str, review_input: DebateReviewInput) -> None:
        """从上次失败的步骤恢复评审（配合 checkpointer 使用）。"""

        async def record_progress(
            stage: str,
            label: str,
            stage_status: str,
            progress_percent: int,
            detail: str | None,
        ) -> None:
            await asyncio.to_thread(
                self.store.mark_stage,
                task_id,
                stage=stage,
                label=label,
                status=RunStageStatus(stage_status),
                progress_percent=progress_percent,
                detail=detail,
            )

        try:
            def record_model_call(metric: Any) -> None:
                self.store.record_model_call(task_id, metric.as_dict())

            result = await self.workflow.aresume(
                review_input,
                thread_id=task_id,
                progress_callback=record_progress,
                model_call_observer=record_model_call,
            )
            self.store.mark_succeeded(task_id, result.model_dump(mode="json"))
        except Exception as exc:
            self.store.mark_failed(task_id, str(exc))

    def get_run(self, task_id: str) -> RunSnapshot | None:
        return self.store.get(task_id)
