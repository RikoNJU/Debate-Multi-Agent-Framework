"""Repositories backed by SQLAlchemy sessions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from ..schemas import DebateReviewInput
from ..services.jobs import RunSnapshot, RunStatus
from .database import Database
from .models import (
    AuditLogRecord,
    AuthSessionRecord,
    HumanReviewRecord,
    PaperArtifactRecord,
    PaperAssignmentRecord,
    PaperRecord,
    PaperRevisionRecord,
    ReviewRunRecord,
    UserRecord,
)
from ..services.security import hash_password, hash_token, new_session_token, verify_password


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SqlAlchemyRunStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        paper_id: str | None = None,
        revision_id: str | None = None,
    ) -> RunSnapshot:
        now = datetime.now(UTC)
        record = ReviewRunRecord(
            task_id=uuid4().hex,
            paper_id=paper_id,
            revision_id=revision_id,
            status=RunStatus.QUEUED.value,
            current_stage="queued",
            created_at=now,
            updated_at=now,
        )
        with self.database.session() as session:
            session.add(record)
        return self._snapshot(record)

    def mark_running(self, task_id: str) -> RunSnapshot:
        return self._update(
            task_id,
            status=RunStatus.RUNNING,
            current_stage="workflow",
            result_json=None,
            error=None,
        )

    def mark_succeeded(self, task_id: str, result: dict[str, Any]) -> RunSnapshot:
        return self._update(
            task_id,
            status=RunStatus.SUCCEEDED,
            current_stage="completed",
            result_json=result,
            error=None,
        )

    def mark_failed(self, task_id: str, error: str) -> RunSnapshot:
        return self._update(
            task_id,
            status=RunStatus.FAILED,
            current_stage="failed",
            result_json=None,
            error=error,
        )

    def get(self, task_id: str) -> RunSnapshot | None:
        with self.database.session() as session:
            record = session.get(ReviewRunRecord, task_id)
            return self._snapshot(record) if record else None

    def list_for_paper(self, paper_id: str) -> list[RunSnapshot]:
        with self.database.session() as session:
            records = session.scalars(
                select(ReviewRunRecord)
                .where(ReviewRunRecord.paper_id == paper_id)
                .order_by(ReviewRunRecord.created_at.desc())
            ).all()
            return [self._snapshot(record) for record in records]

    def mark_interrupted(self) -> int:
        changed = 0
        with self.database.session() as session:
            records = session.scalars(
                select(ReviewRunRecord).where(
                    ReviewRunRecord.status == RunStatus.RUNNING.value
                )
            ).all()
            for record in records:
                record.status = RunStatus.INTERRUPTED.value
                record.current_stage = "interrupted"
                record.error = "服务重启导致评审中断，请重新提交评审任务。"
                record.updated_at = datetime.now(UTC)
                changed += 1
        return changed

    def _update(
        self,
        task_id: str,
        *,
        status: RunStatus,
        current_stage: str,
        result_json: dict[str, Any] | None,
        error: str | None,
    ) -> RunSnapshot:
        with self.database.session() as session:
            record = session.get(ReviewRunRecord, task_id)
            if record is None:
                raise KeyError(task_id)
            record.status = status.value
            record.current_stage = current_stage
            record.result_json = result_json
            record.error = error
            record.updated_at = datetime.now(UTC)
            session.flush()
            return self._snapshot(record)

    @staticmethod
    def _snapshot(record: ReviewRunRecord) -> RunSnapshot:
        return RunSnapshot(
            task_id=record.task_id,
            status=RunStatus(record.status),
            created_at=_aware(record.created_at),
            updated_at=_aware(record.updated_at),
            result=record.result_json,
            error=record.error,
            paper_id=record.paper_id,
            revision_id=record.revision_id,
            current_stage=record.current_stage,
        )


class PaperRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_revision(
        self,
        *,
        review_input: DebateReviewInput,
        source_filename: str,
        revision_id: str,
        pdf_sha256: str,
        pdf_path: str,
        structured_input_path: str,
        mineru_batch_id: str,
        artifacts: list[dict[str, Any]],
    ) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            paper = session.get(PaperRecord, review_input.paper_id)
            if paper is None:
                paper = PaperRecord(
                    id=review_input.paper_id,
                    title=review_input.title,
                    paper_type=(
                        review_input.paper_type.value if review_input.paper_type else None
                    ),
                    source_filename=source_filename,
                    sha256=pdf_sha256,
                    current_revision_id=revision_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(paper)
            else:
                paper.title = review_input.title
                paper.paper_type = (
                    review_input.paper_type.value if review_input.paper_type else None
                )
                paper.source_filename = source_filename
                paper.sha256 = pdf_sha256
                paper.current_revision_id = revision_id
                paper.updated_at = now

            session.add(
                PaperRevisionRecord(
                    id=revision_id,
                    paper_id=review_input.paper_id,
                    sha256=pdf_sha256,
                    pdf_path=pdf_path,
                    structured_input_path=structured_input_path,
                    mineru_batch_id=mineru_batch_id,
                    parse_status="succeeded",
                    created_at=now,
                )
            )
            session.add_all(
                PaperArtifactRecord(
                    id=uuid4().hex,
                    revision_id=revision_id,
                    artifact_type=item["artifact_type"],
                    relative_path=item["relative_path"],
                    file_size=item["file_size"],
                    sha256=item["sha256"],
                    metadata_json=item.get("metadata", {}),
                )
                for item in artifacts
            )

    def get_paper(self, paper_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            paper = session.get(PaperRecord, paper_id)
            if paper is None:
                return None
            revisions = session.scalars(
                select(PaperRevisionRecord)
                .where(PaperRevisionRecord.paper_id == paper_id)
                .order_by(PaperRevisionRecord.created_at.desc())
            ).all()
            return {
                "paper_id": paper.id,
                "title": paper.title,
                "paper_type": paper.paper_type,
                "source_filename": paper.source_filename,
                "sha256": paper.sha256,
                "current_revision_id": paper.current_revision_id,
                "created_at": _aware(paper.created_at),
                "updated_at": _aware(paper.updated_at),
                "revisions": [
                    {
                        "revision_id": revision.id,
                        "sha256": revision.sha256,
                        "mineru_batch_id": revision.mineru_batch_id,
                        "parse_status": revision.parse_status,
                        "created_at": _aware(revision.created_at),
                        "artifact_count": len(revision.artifacts),
                    }
                    for revision in revisions
                ],
            }


class PortalRepository:
    """Persistence boundary for authentication and human-review workflows."""

    def __init__(self, database: Database, *, session_hours: int = 12) -> None:
        self.database = database
        self.session_hours = session_hours

    def ensure_bootstrap_admin(
        self, username: str | None, password: str | None, display_name: str
    ) -> None:
        if not username or not password:
            return
        with self.database.session() as session:
            existing = session.scalar(
                select(UserRecord).where(UserRecord.username == username)
            )
            if existing is None:
                session.add(
                    UserRecord(
                        id=uuid4().hex,
                        username=username,
                        display_name=display_name,
                        role="admin",
                        password_hash=hash_password(password),
                        is_active=True,
                    )
                )

    def create_user(
        self, *, username: str, display_name: str, role: str, password: str
    ) -> dict[str, Any]:
        with self.database.session() as session:
            if session.scalar(select(UserRecord).where(UserRecord.username == username)):
                raise ValueError("用户名已存在")
            record = UserRecord(
                id=uuid4().hex,
                username=username,
                display_name=display_name,
                role=role,
                password_hash=hash_password(password),
                is_active=True,
            )
            session.add(record)
            session.flush()
            return self._user_dict(record)

    def list_users(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            records = session.scalars(
                select(UserRecord).order_by(UserRecord.created_at.asc())
            ).all()
            return [self._user_dict(record) for record in records]

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            record = session.scalar(
                select(UserRecord).where(UserRecord.username == username)
            )
            if (
                record is None
                or not record.is_active
                or not verify_password(password, record.password_hash)
            ):
                return None
            return self._user_dict(record)

    def create_session(self, user_id: str) -> tuple[str, datetime]:
        token = new_session_token()
        expires_at = datetime.now(UTC) + timedelta(hours=self.session_hours)
        with self.database.session() as session:
            session.add(
                AuthSessionRecord(
                    token_hash=hash_token(token),
                    user_id=user_id,
                    expires_at=expires_at,
                )
            )
        return token, expires_at

    def get_user_for_token(self, token: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            auth_session = session.get(AuthSessionRecord, hash_token(token))
            if (
                auth_session is None
                or auth_session.revoked_at is not None
                or _aware(auth_session.expires_at) <= datetime.now(UTC)
            ):
                return None
            user = session.get(UserRecord, auth_session.user_id)
            if user is None or not user.is_active:
                return None
            return self._user_dict(user)

    def revoke_session(self, token: str) -> None:
        with self.database.session() as session:
            auth_session = session.get(AuthSessionRecord, hash_token(token))
            if auth_session is not None and auth_session.revoked_at is None:
                auth_session.revoked_at = datetime.now(UTC)

    def assign_paper(
        self, *, paper_id: str, reviewer_id: str, assigned_by_id: str
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            if session.get(PaperRecord, paper_id) is None:
                raise KeyError("paper")
            reviewer = session.get(UserRecord, reviewer_id)
            if reviewer is None or reviewer.role != "teacher" or not reviewer.is_active:
                raise KeyError("reviewer")
            record = session.scalar(
                select(PaperAssignmentRecord).where(
                    PaperAssignmentRecord.paper_id == paper_id,
                    PaperAssignmentRecord.reviewer_id == reviewer_id,
                )
            )
            if record is None:
                record = PaperAssignmentRecord(
                    id=uuid4().hex,
                    paper_id=paper_id,
                    reviewer_id=reviewer_id,
                    assigned_by_id=assigned_by_id,
                    status="assigned",
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                session.flush()
            self._audit(
                session,
                actor_id=assigned_by_id,
                action="paper.assigned",
                resource_type="assignment",
                resource_id=record.id,
                details={"paper_id": paper_id, "reviewer_id": reviewer_id},
            )
            return self._assignment_dict(session, record)

    def list_assignments_for_teacher(self, reviewer_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            records = session.scalars(
                select(PaperAssignmentRecord)
                .where(PaperAssignmentRecord.reviewer_id == reviewer_id)
                .order_by(PaperAssignmentRecord.updated_at.desc())
            ).all()
            return [self._assignment_dict(session, record) for record in records]

    def get_assignment(
        self, assignment_id: str, *, reviewer_id: str | None = None
    ) -> dict[str, Any] | None:
        with self.database.session() as session:
            record = session.get(PaperAssignmentRecord, assignment_id)
            if record is None or (reviewer_id and record.reviewer_id != reviewer_id):
                return None
            return self._assignment_dict(session, record, include_result=True)

    def current_pdf_path(self, assignment_id: str, *, reviewer_id: str | None) -> str | None:
        with self.database.session() as session:
            assignment = session.get(PaperAssignmentRecord, assignment_id)
            if assignment is None or (
                reviewer_id is not None and assignment.reviewer_id != reviewer_id
            ):
                return None
            paper = session.get(PaperRecord, assignment.paper_id)
            if paper is None:
                return None
            revision = session.get(PaperRevisionRecord, paper.current_revision_id)
            return revision.pdf_path if revision else None

    def save_human_review(
        self,
        *,
        assignment_id: str,
        reviewer_id: str,
        section_scores: list[int],
        advice_content: str,
        teacher_comments: str,
        submit: bool,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            assignment = session.get(PaperAssignmentRecord, assignment_id)
            if assignment is None or assignment.reviewer_id != reviewer_id:
                raise KeyError("assignment")
            existing = session.scalar(
                select(HumanReviewRecord).where(
                    HumanReviewRecord.assignment_id == assignment_id
                )
            )
            if existing is not None and existing.status == "submitted":
                raise ValueError("终审已提交，不能再次修改")
            latest_run = self._latest_run(session, assignment.paper_id)
            total_score = round(sum(section_scores) / 54 * 100)
            review = existing or HumanReviewRecord(
                id=uuid4().hex,
                assignment_id=assignment.id,
                paper_id=assignment.paper_id,
                reviewer_id=reviewer_id,
                total_score=total_score,
            )
            review.ai_task_id = latest_run.task_id if latest_run else None
            review.status = "submitted" if submit else "draft"
            review.section_scores = section_scores
            review.total_score = total_score
            review.advice_content = advice_content
            review.teacher_comments = teacher_comments
            review.updated_at = now
            review.submitted_at = now if submit else None
            if existing is None:
                session.add(review)
            assignment.status = "submitted" if submit else "in_review"
            assignment.updated_at = now
            self._audit(
                session,
                actor_id=reviewer_id,
                action="review.submitted" if submit else "review.draft_saved",
                resource_type="human_review",
                resource_id=review.id,
                details={"assignment_id": assignment_id, "total_score": total_score},
            )
            session.flush()
            return self._review_dict(review)

    def list_admin_papers(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            papers = session.scalars(
                select(PaperRecord).order_by(PaperRecord.updated_at.desc())
            ).all()
            result = []
            for paper in papers:
                assignments = session.scalars(
                    select(PaperAssignmentRecord).where(
                        PaperAssignmentRecord.paper_id == paper.id
                    )
                ).all()
                latest = self._latest_run(session, paper.id)
                result.append(
                    {
                        "paper_id": paper.id,
                        "title": paper.title,
                        "paper_type": paper.paper_type,
                        "source_filename": paper.source_filename,
                        "updated_at": _aware(paper.updated_at),
                        "run_status": latest.status if latest else None,
                        "ai_score": self._ai_score(latest.result_json if latest else None),
                        "assignments": [
                            self._assignment_dict(session, item) for item in assignments
                        ],
                    }
                )
            return result

    def dashboard_statistics(self) -> dict[str, Any]:
        with self.database.session() as session:
            total_papers = session.scalar(select(func.count()).select_from(PaperRecord)) or 0
            total_assignments = (
                session.scalar(select(func.count()).select_from(PaperAssignmentRecord)) or 0
            )
            submitted = session.scalars(
                select(HumanReviewRecord).where(HumanReviewRecord.status == "submitted")
            ).all()
            scores = [item.total_score for item in submitted]
            return {
                "total_papers": total_papers,
                "total_assignments": total_assignments,
                "submitted_reviews": len(submitted),
                "pending_assignments": max(total_assignments - len(submitted), 0),
                "average_human_score": round(sum(scores) / len(scores), 2) if scores else 0,
                "score_distribution": {
                    "excellent": sum(score >= 90 for score in scores),
                    "good": sum(75 <= score < 90 for score in scores),
                    "pass": sum(60 <= score < 75 for score in scores),
                    "fail": sum(score < 60 for score in scores),
                },
            }

    def list_audit_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.session() as session:
            records = session.scalars(
                select(AuditLogRecord)
                .order_by(AuditLogRecord.created_at.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": item.id,
                    "actor_id": item.actor_id,
                    "action": item.action,
                    "resource_type": item.resource_type,
                    "resource_id": item.resource_id,
                    "details": item.details_json,
                    "created_at": _aware(item.created_at),
                }
                for item in records
            ]

    @staticmethod
    def _user_dict(record: UserRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "username": record.username,
            "display_name": record.display_name,
            "role": record.role,
            "is_active": record.is_active,
            "created_at": _aware(record.created_at),
        }

    def _assignment_dict(
        self, session, record: PaperAssignmentRecord, *, include_result: bool = False
    ) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        paper = session.get(PaperRecord, record.paper_id)
        reviewer = session.get(UserRecord, record.reviewer_id)
        review = session.scalar(
            select(HumanReviewRecord).where(
                HumanReviewRecord.assignment_id == record.id
            )
        )
        latest_run = self._latest_run(session, record.paper_id)
        payload = {
            "assignment_id": record.id,
            "paper_id": record.paper_id,
            "title": paper.title if paper else "",
            "paper_type": paper.paper_type if paper else None,
            "source_filename": paper.source_filename if paper else "",
            "reviewer_id": record.reviewer_id,
            "reviewer_name": reviewer.display_name if reviewer else "",
            "status": record.status,
            "ai_task_id": latest_run.task_id if latest_run else None,
            "ai_status": latest_run.status if latest_run else None,
            "ai_score": self._ai_score(latest_run.result_json if latest_run else None),
            "human_review": self._review_dict(review) if review else None,
            "created_at": _aware(record.created_at),
            "updated_at": _aware(record.updated_at),
        }
        if include_result:
            payload["ai_result"] = latest_run.result_json if latest_run else None
        return payload

    @staticmethod
    def _review_dict(record: HumanReviewRecord) -> dict[str, Any]:
        return {
            "review_id": record.id,
            "assignment_id": record.assignment_id,
            "status": record.status,
            "section_scores": record.section_scores,
            "total_score": record.total_score,
            "advice_content": record.advice_content,
            "teacher_comments": record.teacher_comments,
            "ai_task_id": record.ai_task_id,
            "updated_at": _aware(record.updated_at),
            "submitted_at": _aware(record.submitted_at) if record.submitted_at else None,
        }

    @staticmethod
    def _latest_run(session, paper_id: str) -> ReviewRunRecord | None:  # type: ignore[no-untyped-def]
        return session.scalar(
            select(ReviewRunRecord)
            .where(ReviewRunRecord.paper_id == paper_id)
            .order_by(ReviewRunRecord.created_at.desc())
            .limit(1)
        )

    @staticmethod
    def _ai_score(result: dict[str, Any] | None) -> int | float | None:
        if not result:
            return None
        value = result.get("final_score", {}).get("total_score")
        return value if isinstance(value, (int, float)) else None

    @staticmethod
    def _audit(
        session,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any],
    ) -> None:  # type: ignore[no-untyped-def]
        session.add(
            AuditLogRecord(
                id=uuid4().hex,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details_json=details,
            )
        )
