"""Academic-administration endpoints for users, assignments, and oversight."""

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..persistence import PortalRepository
from ..schemas import (
    AdminPaperResponse,
    AssignmentCreateRequest,
    AssignmentResponse,
    AuditLogResponse,
    DashboardStatistics,
    UserCreateRequest,
    UserResponse,
)
from .dependencies import get_portal_repository, require_roles

router = APIRouter(prefix="/portal/admin", tags=["portal-admin"])
admin_only = require_roles("admin")


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _user: dict[str, Any] = Depends(admin_only),
    repository: PortalRepository = Depends(get_portal_repository),
) -> list[UserResponse]:
    return [UserResponse.model_validate(item) for item in repository.list_users()]


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    payload: UserCreateRequest,
    _user: dict[str, Any] = Depends(admin_only),
    repository: PortalRepository = Depends(get_portal_repository),
) -> UserResponse:
    try:
        return UserResponse.model_validate(
            repository.create_user(
                username=payload.username,
                display_name=payload.display_name,
                role=payload.role,
                password=payload.password,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/papers", response_model=list[AdminPaperResponse])
async def list_papers(
    _user: dict[str, Any] = Depends(admin_only),
    repository: PortalRepository = Depends(get_portal_repository),
) -> list[AdminPaperResponse]:
    return [
        AdminPaperResponse.model_validate(item)
        for item in repository.list_admin_papers()
    ]


@router.post("/assignments", response_model=AssignmentResponse, status_code=201)
async def create_assignment(
    payload: AssignmentCreateRequest,
    user: dict[str, Any] = Depends(admin_only),
    repository: PortalRepository = Depends(get_portal_repository),
) -> AssignmentResponse:
    try:
        return AssignmentResponse.model_validate(
            repository.assign_paper(
                paper_id=payload.paper_id,
                reviewer_id=payload.reviewer_id,
                assigned_by_id=user["id"],
            )
        )
    except KeyError as exc:
        detail = "论文不存在" if exc.args[0] == "paper" else "教师不存在或不可用"
        raise HTTPException(status_code=404, detail=detail) from exc


@router.get("/statistics", response_model=DashboardStatistics)
async def statistics(
    _user: dict[str, Any] = Depends(admin_only),
    repository: PortalRepository = Depends(get_portal_repository),
) -> DashboardStatistics:
    return DashboardStatistics.model_validate(repository.dashboard_statistics())


@router.get("/audit-logs", response_model=list[AuditLogResponse])
async def audit_logs(
    _user: dict[str, Any] = Depends(admin_only),
    repository: PortalRepository = Depends(get_portal_repository),
) -> list[AuditLogResponse]:
    return [
        AuditLogResponse.model_validate(item)
        for item in repository.list_audit_logs()
    ]


@router.get("/exports/reviews.csv")
async def export_reviews(
    _user: dict[str, Any] = Depends(admin_only),
    repository: PortalRepository = Depends(get_portal_repository),
) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["论文ID", "论文标题", "论文类型", "AI评分", "教师", "人工评分", "状态"])
    for paper in repository.list_admin_papers():
        if not paper["assignments"]:
            writer.writerow(
                [paper["paper_id"], paper["title"], paper["paper_type"] or "", paper["ai_score"] or "", "", "", "未分配"]
            )
        for assignment in paper["assignments"]:
            review = assignment["human_review"] or {}
            writer.writerow(
                [paper["paper_id"], paper["title"], paper["paper_type"] or "", paper["ai_score"] or "", assignment["reviewer_name"], review.get("total_score", ""), assignment["status"]]
            )
    filename = "review-results.csv"
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
