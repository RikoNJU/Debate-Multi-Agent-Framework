"""Paper ingestion API backed by the MinerU cloud adapter."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile

from ..ingestion import (
    InvalidPdfError,
    MarkdownPaperParser,
    MinerUContentListAdapter,
    MinerUClient,
    MinerUConfig,
    MinerUConfigurationError,
    MinerUError,
    MinerUTimeoutError,
)
from ..schemas import (
    MinerUParseResponse,
    PaperDetailResponse,
    PaperReviewSubmission,
    PaperType,
)
from ..services import DebateWorkflowService
from ..services.jobs import RunSnapshot
from ..services.paper_storage import PaperPersistenceService
from .dependencies import get_debate_workflow_service, get_paper_persistence_service

router = APIRouter(prefix="/papers", tags=["papers"])


@router.post("/parse", response_model=MinerUParseResponse)
async def parse_paper(
    request: Request, pdf: UploadFile = File(...)
) -> MinerUParseResponse:
    """Convert one PDF to MinerU Markdown and structured artifacts."""

    if not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")
    config = MinerUConfig.from_env()
    output_root = Path(request.app.state.settings.mineru_output_dir)
    try:
        with tempfile.TemporaryDirectory(prefix="debate-mineru-upload-") as temp_dir:
            pdf_path = Path(temp_dir) / "paper.pdf"
            size = 0
            with pdf_path.open("wb") as target:
                while chunk := await pdf.read(1024 * 1024):
                    size += len(chunk)
                    if size > config.max_pdf_bytes:
                        raise InvalidPdfError("PDF exceeds configured size limit")
                    target.write(chunk)
            result = await MinerUClient(config).parse_pdf(
                pdf_path,
                output_root=output_root,
            )
            return MinerUParseResponse.from_result(result)
    except InvalidPdfError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MinerUConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MinerUTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except MinerUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await pdf.close()


@router.post("/review", response_model=PaperReviewSubmission, status_code=202)
async def parse_and_review_paper(
    request: Request,
    background_tasks: BackgroundTasks,
    pdf: UploadFile = File(...),
    paper_type: PaperType | None = Form(None),
    paper_id: str | None = Form(None),
    title: str | None = Form(None),
    service: DebateWorkflowService = Depends(get_debate_workflow_service),
    persistence: PaperPersistenceService = Depends(get_paper_persistence_service),
) -> PaperReviewSubmission:
    """Parse a PDF, build structured input, and enqueue the review workflow."""

    if not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")
    if paper_id and (
        len(paper_id) > 255 or "/" in paper_id or "\\" in paper_id
    ):
        raise HTTPException(status_code=422, detail="paper_id 格式不合法")
    config = MinerUConfig.from_env()
    output_root = Path(request.app.state.settings.mineru_output_dir)
    try:
        with tempfile.TemporaryDirectory(prefix="debate-mineru-upload-") as temp_dir:
            pdf_path = Path(temp_dir) / "paper.pdf"
            size = 0
            with pdf_path.open("wb") as target:
                while chunk := await pdf.read(1024 * 1024):
                    size += len(chunk)
                    if size > config.max_pdf_bytes:
                        raise InvalidPdfError("PDF exceeds configured size limit")
                    target.write(chunk)
            parsed = await MinerUClient(config).parse_pdf(
                pdf_path,
                output_root=output_root,
            )
            review_input = MarkdownPaperParser().parse(
                parsed.markdown,
                paper_type=paper_type,
                paper_id=paper_id,
                title=title,
                source_filename=pdf.filename,
                mineru_batch_id=parsed.batch_id,
            )
            if parsed.content_list_path:
                review_input = MinerUContentListAdapter().enrich(
                    review_input, parsed.content_list_path
                )
            persisted = await asyncio.to_thread(
                persistence.persist,
                review_input=review_input,
                parsed=parsed,
                source_pdf=pdf_path,
                source_filename=pdf.filename,
            )
        snapshot = service.create_run(
            paper_id=review_input.paper_id,
            revision_id=persisted.revision_id,
        )
        background_tasks.add_task(service.execute, snapshot.task_id, review_input)
        return PaperReviewSubmission(
            task_id=snapshot.task_id,
            status=snapshot.status.value,
            paper_id=review_input.paper_id,
            title=review_input.title,
            chapter_count=len(review_input.chapters),
            batch_id=parsed.batch_id,
        )
    except InvalidPdfError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MinerUConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MinerUTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except MinerUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await pdf.close()


@router.get("/{paper_id}", response_model=PaperDetailResponse)
async def get_paper(
    request: Request,
    paper_id: str,
) -> PaperDetailResponse:
    paper = request.app.state.paper_repository.get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    return PaperDetailResponse.model_validate(paper)


@router.get("/{paper_id}/runs", response_model=list[RunSnapshot])
async def list_paper_runs(
    request: Request,
    paper_id: str,
) -> list[RunSnapshot]:
    paper = request.app.state.paper_repository.get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    return request.app.state.run_store.list_for_paper(paper_id)
