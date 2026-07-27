"""Debate 论文评审任务 API。"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from debate_agent_framework.schemas import DebateReviewInput
from debate_agent_framework.services.jobs import RunSnapshot

from ..services import DebateWorkflowService, get_debate_workflow_service

router = APIRouter(prefix="/runs", tags=["debate-runs"])


@router.post("", response_model=RunSnapshot, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    review_input: DebateReviewInput,
    background_tasks: BackgroundTasks,
    service: DebateWorkflowService = Depends(get_debate_workflow_service),
) -> RunSnapshot:
    snapshot = service.create_run()
    background_tasks.add_task(service.execute, snapshot.task_id, review_input)
    return snapshot


@router.get("/{task_id}", response_model=RunSnapshot)
async def get_run(
    task_id: str,
    service: DebateWorkflowService = Depends(get_debate_workflow_service),
) -> RunSnapshot:
    snapshot = service.get_run(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Debate 评审任务不存在")
    return snapshot
