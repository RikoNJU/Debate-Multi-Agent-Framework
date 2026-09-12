"""Anonymous student endpoints protected by per-task access codes."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
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
    repository: PortalRepository = Depends(get_portal_repository),
    storage: PaperPersistenceService = Depends(get_paper_persistence_service),
) -> FileResponse:
    value = repository.pdf_path_for_task(task_id)
    if value is None:
        raise HTTPException(status_code=404, detail="论文文件不存在")
    try:
        path = storage.resolve_stored_path(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail="论文文件不存在"
        ) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="论文文件不存在")
    return FileResponse(path, media_type="application/pdf", filename="paper.pdf")


@router.get("/tasks/{task_id}/review-table")
async def get_review_table(
    task_id: str,
    request: Request,
    repository: PortalRepository = Depends(get_portal_repository),
    storage: PaperPersistenceService = Depends(get_paper_persistence_service),
    service: DebateWorkflowService = Depends(get_debate_workflow_service),
) -> FileResponse:
    """导出 18 维评审表。

    教师终审已发布时以教师评分为准；否则回退到 AI 预审评分生成预览版。
    """
    snapshot = service.get_run(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Debate 评审任务不存在")
    if not snapshot.paper_id:
        raise HTTPException(status_code=409, detail="该任务未关联论文，无法导出评审表")
    paper = storage.repository.get_paper(snapshot.paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")

    published = repository.get_published_review_for_paper(snapshot.paper_id)
    result = snapshot.result if isinstance(snapshot.result, dict) else {}
    final_score = result.get("final_score") or {}
    chapter_advice = chapter_advice_from_result(result)

    if published and published.get("section_scores"):
        section_scores = [int(v) for v in published["section_scores"]]
        total_score = int(published["total_score"])
        advice_content = published.get("advice_content") or ""
        stem = f"review_table_{published['review_id']}"
        filename = f"18维评审表-{paper['title']}.pdf"
    else:
        levels = final_score.get("legacy_level_scores")
        raw_total = final_score.get("total_score")
        if not isinstance(levels, list) or len(levels) != 18 or raw_total is None:
            raise HTTPException(
                status_code=409, detail="AI 预审评分尚未生成，暂时无法导出 18 维评审表"
            )
        section_scores = [max(0, min(3, int(v))) for v in levels]
        total_score = int(round(float(raw_total)))
        advice_content = final_score.get("overall_evaluation") or ""
        stem = f"review_table_ai_{task_id}"
        filename = f"18维评审表(AI预审)-{paper['title']}.pdf"

    try:
        output_dir = storage.review_table_dir(snapshot.paper_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="论文不存在") from exc
    data = build_review_table_data(
        paper_title=paper["title"],
        source_filename=paper.get("source_filename"),
        paper_type=paper.get("paper_type"),
        reviewer_name="",
        section_scores=section_scores,
        total_score=total_score,
        advice_content=advice_content,
        chapter_advice=chapter_advice,
    )
    try:
        pdf_path = await asyncio.to_thread(
            compile_review_table_pdf,
            data=data,
            output_dir=output_dir,
            stem=stem,
            tectonic_path=request.app.state.settings.tectonic_path,
            cache_dir=str(request.app.state.data_dir / ".tectonic-cache"),
        )
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(pdf_path, media_type="application/pdf", filename=filename)
