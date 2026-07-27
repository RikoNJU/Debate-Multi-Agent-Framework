"""Debate 论文评审任务的应用服务。"""

from __future__ import annotations

from functools import lru_cache

from debate_agent_framework.models import DebateReviewInput
from debate_agent_framework.services.jobs import InMemoryRunStore, RunSnapshot
from debate_agent_framework.workflows import DebateWorkflow


class DebateWorkflowService:
    def __init__(
        self,
        workflow: DebateWorkflow | None = None,
        store: InMemoryRunStore | None = None,
    ) -> None:
        self.workflow = workflow or DebateWorkflow.default()
        self.store = store or InMemoryRunStore()

    def create_run(self) -> RunSnapshot:
        return self.store.create()

    async def execute(self, task_id: str, review_input: DebateReviewInput) -> None:
        self.store.mark_running(task_id)
        try:
            result = await self.workflow.arun(review_input)
            self.store.mark_succeeded(task_id, result.model_dump(mode="json"))
        except Exception as exc:
            self.store.mark_failed(task_id, str(exc))

    def get_run(self, task_id: str) -> RunSnapshot | None:
        return self.store.get(task_id)


@lru_cache(maxsize=1)
def get_debate_workflow_service() -> DebateWorkflowService:
    return DebateWorkflowService()
