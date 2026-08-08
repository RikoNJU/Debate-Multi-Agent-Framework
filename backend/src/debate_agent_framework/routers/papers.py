"""Paper ingestion API backed by the MinerU cloud adapter."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile

from ..ingestion import (
    InvalidPdfError,
    MarkdownPaperParser,
    MinerUClient,
    MinerUConfig,
    MinerUConfigurationError,
    MinerUError,
    MinerUTimeoutError,
)
from ..schemas import MinerUParseResponse, PaperReviewSubmission, PaperType
from ..services import DebateWorkflowService, get_debate_workflow_service

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
    paper_type: PaperType = Form(...),
    paper_id: str | None = Form(None),
    title: str | None = Form(None),
    service: DebateWorkflowService = Depends(get_debate_workflow_service),
) -> PaperReviewSubmission:
    """Parse a PDF, build structured input, and enqueue the review workflow."""

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
        snapshot = service.create_run()
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
