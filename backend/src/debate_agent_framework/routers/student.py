"""Anonymous student endpoints protected by per-task access codes."""

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse

from ..persistence import PortalRepository
from ..services.paper_storage import PaperPersistenceService
from ..services.review_table_export import (
    build_review_table_data,
    chapter_advice_from_result,
    compile_review_table_pdf,
)
from ..services.workflow_service import DebateWorkflowService
from .dependencies import (
    get_debate_workflow_service,
    get_paper_persistence_service,
    get_portal_repository,
)

router = APIRouter(prefix="/student", tags=["student"])


@router.get("/tasks/{task_id}/pdf")
async def get_student_pdf(
    task_id: str,
    access_token: str | None = Header(None, alias="X-Submission-Token"),
    repository: PortalRepository = Depends(get_portal_repository),
    storage: PaperPersistenceService = Depends(get_paper_persistence_service),
) -> FileResponse:
    if not access_token:
        raise HTTPException(status_code=401, detail="需要任务访问码")
    value = repository.student_pdf_path(task_id, access_token)
    if value is None:
        raise HTTPException(status_code=404, detail="论文文件不存在或访问码无效")
    try:
        path = storage.resolve_stored_path(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail="论文文件不存在或访问码无效"
        ) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="论文文件不存在或访问码无效")
    return FileResponse(path, media_type="application/pdf", filename="paper.pdf")


@router.get("/tasks/{task_id}/review-table")
async def get_review_table(
    task_id: str,
    request: Request,
    access_token: str | None = Header(None, alias="X-Submission-Token"),
    repository: PortalRepository = Depends(get_portal_repository),
    storage: PaperPersistenceService = Depends(get_paper_persistence_service),
    service: DebateWorkflowService = Depends(get_debate_workflow_service),
) -> FileResponse:
    """导出 18 维评审表（以已发布的人工终审为准）。"""
    if not access_token:
        raise HTTPException(status_code=401, detail="需要任务访问码")
    if not repository.validate_student_task_access(task_id, access_token):
        raise HTTPException(status_code=403, detail="任务访问码无效")
    snapshot = service.get_run(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Debate 评审任务不存在")
    if not snapshot.paper_id:
        raise HTTPException(status_code=409, detail="该任务未关联论文，无法导出评审表")
    published = repository.get_published_review_for_paper(snapshot.paper_id)
    if published is None or not published.get("section_scores"):
        raise HTTPException(
            status_code=409, detail="人工终审尚未发布，暂时无法导出 18 维评审表"
        )
    paper = storage.repository.get_paper(snapshot.paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    try:
        output_dir = storage.review_table_dir(snapshot.paper_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="论文不存在") from exc
    data = build_review_table_data(
        paper_title=paper["title"],
        source_filename=paper.get("source_filename"),
        paper_type=paper.get("paper_type"),
        reviewer_name="",
        section_scores=list(published["section_scores"]),
        total_score=published["total_score"],
        advice_content=published.get("advice_content") or "",
        chapter_advice=chapter_advice_from_result(
            snapshot.result if snapshot.result else None
        ),
    )
    try:
        pdf_path = await asyncio.to_thread(
            compile_review_table_pdf,
            data=data,
            output_dir=output_dir,
            stem=f"review_table_{published['review_id']}",
            tectonic_path=request.app.state.settings.tectonic_path,
            cache_dir=f"{request.app.state.settings.data_dir}/.tectonic-cache",
        )
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    filename = f"18维评审表-{paper['title']}.pdf"
    return FileResponse(pdf_path, media_type="application/pdf", filename=filename)
