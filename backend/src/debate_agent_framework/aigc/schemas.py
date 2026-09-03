from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AigcModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AigcTaskStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    DETECTING = "detecting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class AigcRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AigcLocator(AigcModel):
    block_id: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)
    bbox: dict[str, float] | None = None


class AigcSegment(AigcModel):
    segment_id: str = Field(min_length=1)
    chapter_id: str | None = None
    chapter_name: str = "未知章节"
    text: str = Field(min_length=1)
    text_sha256: str = Field(min_length=64, max_length=64)
    token_count: int = Field(ge=1)
    locators: list[AigcLocator] = Field(default_factory=list)


class AigcSegmentResult(AigcModel):
    segment_id: str
    chapter_id: str | None = None
    chapter_name: str
    content_preview: str
    text_sha256: str
    token_count: int
    ai_probability: float = Field(ge=0.0, le=1.0)
    risk_level: AigcRiskLevel
    locators: list[AigcLocator] = Field(default_factory=list)


class AigcChapterSummary(AigcModel):
    chapter_id: str | None = None
    chapter_name: str
    segment_count: int
    token_count: int
    average_risk_score: float = Field(ge=0.0, le=1.0)
    medium_risk_ratio: float = Field(ge=0.0, le=1.0)
    high_risk_ratio: float = Field(ge=0.0, le=1.0)


class AigcDetectionResult(AigcModel):
    model_id: str
    model_revision: str | None = None
    preprocessing_version: str
    calibrated: bool = False
    disclaimer: str = (
        "检测结果仅用于辅助筛查，不能单独作为认定 AI 生成或学术不端的依据。"
    )
    segment_count: int
    token_count: int
    average_risk_score: float = Field(ge=0.0, le=1.0)
    medium_risk_ratio: float = Field(ge=0.0, le=1.0)
    high_risk_ratio: float = Field(ge=0.0, le=1.0)
    chapters: list[AigcChapterSummary] = Field(default_factory=list)
    segments: list[AigcSegmentResult] = Field(default_factory=list)


class AigcTaskCreated(AigcModel):
    task_id: str
    access_code: str
    status: AigcTaskStatus
    created_at: datetime


class AigcTaskSnapshot(AigcModel):
    task_id: str
    source_filename: str
    status: AigcTaskStatus
    current_stage: str
    progress_percent: int = Field(ge=0, le=100)
    result: AigcDetectionResult | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class AigcAvailability(AigcModel):
    enabled: bool
    ready: bool
    model_id: str
    dependencies_installed: bool
    mineru_configured: bool
    message: str


def snapshot_from_mapping(value: dict[str, Any]) -> AigcTaskSnapshot:
    return AigcTaskSnapshot.model_validate(value)
