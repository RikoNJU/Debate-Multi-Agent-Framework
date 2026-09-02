"""API contracts for teacher and academic-administration workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field



class PortalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserResponse(PortalModel):
    id: str
    username: str
    display_name: str
    role: Literal["teacher", "admin"]
    is_active: bool
    created_at: datetime


class LoginRequest(PortalModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(PortalModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserResponse


class UserCreateRequest(PortalModel):
    username: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=128)
    role: Literal["teacher", "admin"]
    password: str = Field(min_length=8, max_length=256)


class AssignmentCreateRequest(PortalModel):
    paper_id: str = Field(min_length=1, max_length=255)
    reviewer_id: str = Field(min_length=1, max_length=32)


ScoreValue = Annotated[int, Field(ge=0, le=3)]


class HumanReviewUpsert(PortalModel):
    section_scores: list[ScoreValue] = Field(min_length=18, max_length=18)
    advice_content: str = Field(default="", max_length=20_000)
    teacher_comments: str = Field(default="", max_length=20_000)


class HumanReviewResponse(PortalModel):
    review_id: str
    assignment_id: str
    status: Literal["draft", "submitted"]
    section_scores: list[int]
    total_score: int
    advice_content: str
    teacher_comments: str
    ai_task_id: str | None = None
    updated_at: datetime
    submitted_at: datetime | None = None
    published_at: datetime | None = None


class StudentPublishedReview(PortalModel):
    review_id: str
    section_scores: list[int]
    total_score: int
    advice_content: str
    submitted_at: datetime | None = None
    published_at: datetime


class StudentTaskResponse(PortalModel):
    task_id: str
    status: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None
    error: str | None = None
    paper_id: str | None = None
    paper_title: str | None = None
    revision_id: str | None = None
    finding_identity_version: str | None = None
    model_usage: dict[str, Any] = Field(default_factory=dict)
    current_stage: str | None = None
    current_stage_label: str | None = None
    progress_percent: int = Field(default=0, ge=0, le=100)
    stage_started_at: datetime | None = None
    stage_events: list[dict[str, Any]] = Field(default_factory=list)
    published_review: StudentPublishedReview | None = None


class RunSubmissionResponse(StudentTaskResponse):
    pass


class AssignmentResponse(PortalModel):
    assignment_id: str
    paper_id: str
    title: str
    paper_type: str | None = None
    source_filename: str
    reviewer_id: str
    reviewer_name: str
    status: Literal["assigned", "in_review", "submitted"]
    ai_task_id: str | None = None
    ai_status: str | None = None
    ai_score: float | None = None
    ai_section_scores: list[int] | None = None
    human_review: HumanReviewResponse | None = None
    ai_result: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class AdminPaperResponse(PortalModel):
    paper_id: str
    title: str
    paper_type: str | None = None
    source_filename: str
    updated_at: datetime
    run_status: str | None = None
    ai_score: float | None = None
    assignments: list[AssignmentResponse]


class DashboardStatistics(PortalModel):
    total_papers: int
    total_assignments: int
    submitted_reviews: int
    pending_assignments: int
    average_human_score: float
    score_distribution: dict[str, int]


class ReviewCriterion(PortalModel):
    id: int
    name: str
    category: Literal["format", "content"]
    description: str


class AuditLogResponse(PortalModel):
    id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    details: dict[str, Any]
    created_at: datetime
