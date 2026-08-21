"""Debate 论文评审任务 API。"""

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from debate_agent_framework.schemas import DebateReviewInput, RunSubmissionResponse, StudentTaskResponse
from debate_agent_framework.services.jobs import RunSnapshot, RunStatus

from ..persistence import PortalRepository
from ..services import DebateWorkflowService
from ..services.paper_storage import PaperPersistenceService
from .dependencies import (
    get_debate_workflow_service,
    get_paper_persistence_service,
    get_portal_repository,
)

router = APIRouter(prefix="/runs", tags=["debate-runs"])


@router.post("", response_model=RunSubmissionResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    review_input: DebateReviewInput,
    background_tasks: BackgroundTasks,
    service: DebateWorkflowService = Depends(get_debate_workflow_service),
) -> RunSubmissionResponse:
    snapshot = service.create_run()
    background_tasks.add_task(service.execute, snapshot.task_id, review_input)
    return RunSubmissionResponse(
        **snapshot.model_dump(), published_review=None
    )


@router.post(
    "/{task_id}/retry",
    response_model=RunSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_run(
    task_id: str,
    background_tasks: BackgroundTasks,
    service: DebateWorkflowService = Depends(get_debate_workflow_service),
    persistence: PaperPersistenceService = Depends(get_paper_persistence_service),
) -> RunSubmissionResponse:
    failed = service.get_run(task_id)
    if failed is None:
        raise HTTPException(status_code=404, detail="Debate 评审任务不存在")
    if failed.status not in {RunStatus.FAILED, RunStatus.INTERRUPTED}:
        raise HTTPException(status_code=409, detail="只有失败或中断的任务可以重新评审")
    if not failed.paper_id or not failed.revision_id:
        raise HTTPException(status_code=409, detail="该任务没有可复用的论文结构化数据")
    try:
        review_input = await asyncio.to_thread(
            persistence.load_review_input, failed.revision_id
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if review_input.paper_id != failed.paper_id:
        raise HTTPException(status_code=409, detail="论文结构化数据与任务不一致")

    # 保留同一任务编号和已完成步骤的进度，从断点继续执行
    snapshot = service.prepare_resume(task_id)
    background_tasks.add_task(service.resume_run, task_id, review_input)
    return RunSubmissionResponse(
        **snapshot.model_dump(),
        published_review=None,
    )


@router.get("/{task_id}", response_model=StudentTaskResponse)
async def get_run(
    task_id: str,
    service: DebateWorkflowService = Depends(get_debate_workflow_service),
    portal: PortalRepository = Depends(get_portal_repository),
) -> StudentTaskResponse:
    snapshot = service.get_run(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Debate 评审任务不存在")
    published = (
        portal.get_published_review_for_paper(snapshot.paper_id)
        if snapshot.paper_id
        else None
    )
    return StudentTaskResponse(
        **snapshot.model_dump(),
        paper_title=portal.paper_title_for_task(task_id),
        published_review=published,
    )
