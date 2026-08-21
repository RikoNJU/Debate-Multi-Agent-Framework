"""Teacher paper-reading and human-review endpoints."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ..persistence import PortalRepository
from ..services.paper_storage import PaperPersistenceService
from ..services.review_table_export import (
    build_review_table_data,
    chapter_advice_from_result,
    compile_review_table_pdf,
)
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


@router.get("/assignments/{assignment_id}/review-table")
async def export_review_table(
    assignment_id: str,
    request: Request,
    user: dict[str, Any] = Depends(teacher_or_admin),
    repository: PortalRepository = Depends(get_portal_repository),
    storage: PaperPersistenceService = Depends(get_paper_persistence_service),
) -> FileResponse:
    """导出 18 维评审表：生成 LaTeX、编译 PDF 并保存到论文持久化目录。"""
    item = repository.get_assignment(assignment_id, reviewer_id=user["id"])
    if item is None:
        raise HTTPException(status_code=404, detail="评审任务不存在")
    review = item.get("human_review")
    if review is None:
        raise HTTPException(
            status_code=409, detail="尚未完成评分，请先保存或提交终审后再导出评审表"
        )
    section_scores = list(review["section_scores"])
    if len(section_scores) != 18:
        raise HTTPException(status_code=409, detail="尚未完成评分，无法导出评审表")
    techniques = chapter_advice_from_result(item.get("ai_result"))
    try:
        output_dir = storage.review_table_dir(item["paper_id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="论文不存在") from exc
    data = build_review_table_data(
        paper_title=item["title"],
        source_filename=item.get("source_filename"),
        paper_type=item.get("paper_type"),
        reviewer_name=item.get("reviewer_name"),
        section_scores=list(review["section_scores"]),
        total_score=review["total_score"],
        advice_content=review.get("advice_content") or "",
        chapter_advice=techniques,
    )
    try:
        pdf_path = await asyncio.to_thread(
            compile_review_table_pdf,
            data=data,
            output_dir=output_dir,
            stem=f"review_table_{review['review_id']}",
        )
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    filename = f"18维评审表-{item['title']}.pdf"
    return FileResponse(pdf_path, media_type="application/pdf", filename=filename)
