"""Anonymous student endpoints protected by per-task access codes."""

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse

from ..persistence import PortalRepository
from ..services.paper_storage import PaperPersistenceService
from .dependencies import get_paper_persistence_service, get_portal_repository

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
