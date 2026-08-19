"""Anonymous student endpoints protected by per-task access codes."""

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse

from ..persistence import PortalRepository
from .dependencies import get_portal_repository

router = APIRouter(prefix="/student", tags=["student"])


@router.get("/tasks/{task_id}/pdf")
async def get_student_pdf(
    task_id: str,
    access_token: str | None = Header(None, alias="X-Submission-Token"),
    repository: PortalRepository = Depends(get_portal_repository),
) -> FileResponse:
    if not access_token:
        raise HTTPException(status_code=401, detail="需要任务访问码")
    value = repository.student_pdf_path(task_id, access_token)
    if value is None or not Path(value).is_file():
        raise HTTPException(status_code=404, detail="论文文件不存在或访问码无效")
    return FileResponse(Path(value), media_type="application/pdf", filename="paper.pdf")
