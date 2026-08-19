"""Teacher paper-reading and human-review endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..persistence import PortalRepository
from ..services.paper_storage import PaperPersistenceService
from ..schemas import (
    AssignmentResponse,
    HumanReviewResponse,
    HumanReviewUpsert,
    ReviewCriterion,
)
from ..services.review_criteria import REVIEW_CRITERIA
from .dependencies import (
    get_paper_persistence_service,
    get_portal_repository,
    require_roles,
)

router = APIRouter(prefix="/portal/teacher", tags=["portal-teacher"])
teacher_or_admin = require_roles("teacher", "admin")


@router.get("/criteria", response_model=list[ReviewCriterion])
async def criteria(
    _user: dict[str, Any] = Depends(teacher_or_admin),
) -> list[ReviewCriterion]:
    return [ReviewCriterion.model_validate(item) for item in REVIEW_CRITERIA]


@router.get("/assignments", response_model=list[AssignmentResponse])
async def list_assignments(
    user: dict[str, Any] = Depends(teacher_or_admin),
    repository: PortalRepository = Depends(get_portal_repository),
) -> list[AssignmentResponse]:
    return [
        AssignmentResponse.model_validate(item)
        for item in repository.list_assignments_for_teacher(user["id"])
    ]


@router.get("/assignments/{assignment_id}", response_model=AssignmentResponse)
async def get_assignment(
    assignment_id: str,
    user: dict[str, Any] = Depends(teacher_or_admin),
    repository: PortalRepository = Depends(get_portal_repository),
) -> AssignmentResponse:
    item = repository.get_assignment(assignment_id, reviewer_id=user["id"])
    if item is None:
        raise HTTPException(status_code=404, detail="评审任务不存在")
    return AssignmentResponse.model_validate(item)


@router.get("/assignments/{assignment_id}/pdf")
async def get_assignment_pdf(
    assignment_id: str,
    user: dict[str, Any] = Depends(teacher_or_admin),
    repository: PortalRepository = Depends(get_portal_repository),
    storage: PaperPersistenceService = Depends(get_paper_persistence_service),
) -> FileResponse:
    value = repository.current_pdf_path(assignment_id, reviewer_id=user["id"])
    if value is None:
        raise HTTPException(status_code=404, detail="论文文件不存在")
    try:
        path = storage.resolve_stored_path(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="论文文件不存在") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="论文文件不存在")
    return FileResponse(path, media_type="application/pdf", filename="paper.pdf")


def _save_review(
    assignment_id: str,
    payload: HumanReviewUpsert,
    user: dict[str, Any],
    repository: PortalRepository,
    *,
    submit: bool,
) -> HumanReviewResponse:
    try:
        result = repository.save_human_review(
            assignment_id=assignment_id,
            reviewer_id=user["id"],
            section_scores=payload.section_scores,
            advice_content=payload.advice_content,
            teacher_comments=payload.teacher_comments,
            submit=submit,
        )
        return HumanReviewResponse.model_validate(result)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="评审任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put(
    "/assignments/{assignment_id}/review",
    response_model=HumanReviewResponse,
)
async def save_review_draft(
    assignment_id: str,
    payload: HumanReviewUpsert,
    user: dict[str, Any] = Depends(teacher_or_admin),
    repository: PortalRepository = Depends(get_portal_repository),
) -> HumanReviewResponse:
    return _save_review(assignment_id, payload, user, repository, submit=False)


@router.post(
    "/assignments/{assignment_id}/review/submit",
    response_model=HumanReviewResponse,
)
async def submit_review(
    assignment_id: str,
    payload: HumanReviewUpsert,
    user: dict[str, Any] = Depends(teacher_or_admin),
    repository: PortalRepository = Depends(get_portal_repository),
) -> HumanReviewResponse:
    return _save_review(assignment_id, payload, user, repository, submit=True)
