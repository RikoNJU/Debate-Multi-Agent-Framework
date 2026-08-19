"""Debate 论文评审任务 API。"""

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status

from debate_agent_framework.schemas import DebateReviewInput, RunSubmissionResponse, StudentTaskResponse
from debate_agent_framework.services.jobs import RunSnapshot

from ..services import DebateWorkflowService
from .dependencies import get_debate_workflow_service
from ..persistence import PortalRepository
from .dependencies import get_portal_repository

router = APIRouter(prefix="/runs", tags=["debate-runs"])


@router.post("", response_model=RunSubmissionResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    review_input: DebateReviewInput,
    background_tasks: BackgroundTasks,
    service: DebateWorkflowService = Depends(get_debate_workflow_service),
    portal: PortalRepository = Depends(get_portal_repository),
) -> RunSubmissionResponse:
    snapshot = service.create_run()
    access_token = portal.issue_student_access(task_id=snapshot.task_id, paper_id=None)
    background_tasks.add_task(service.execute, snapshot.task_id, review_input)
    return RunSubmissionResponse(
        **snapshot.model_dump(), access_token=access_token, published_review=None
    )


@router.get("/{task_id}", response_model=StudentTaskResponse)
async def get_run(
    task_id: str,
    access_token: str | None = Header(None, alias="X-Submission-Token"),
    service: DebateWorkflowService = Depends(get_debate_workflow_service),
    portal: PortalRepository = Depends(get_portal_repository),
) -> StudentTaskResponse:
    if not access_token:
        raise HTTPException(status_code=401, detail="需要任务访问码")
    if not portal.validate_student_task_access(task_id, access_token):
        raise HTTPException(status_code=403, detail="任务访问码无效")
    snapshot = service.get_run(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Debate 评审任务不存在")
    published = (
        portal.get_published_review_for_paper(snapshot.paper_id)
        if snapshot.paper_id
        else None
    )
    return StudentTaskResponse(
        **snapshot.model_dump(), published_review=published
    )
