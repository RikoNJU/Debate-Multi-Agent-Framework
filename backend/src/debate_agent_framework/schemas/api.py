"""Debate 论文评审 Web API 响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    application: str
    workflow: str


class PaperRevisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str
    sha256: str
    mineru_batch_id: str
    parse_status: str
    created_at: datetime
    artifact_count: int


class PaperDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    title: str
    paper_type: str | None = None
    source_filename: str
    sha256: str
    current_revision_id: str
    created_at: datetime
    updated_at: datetime
    revisions: list[PaperRevisionSummary]
