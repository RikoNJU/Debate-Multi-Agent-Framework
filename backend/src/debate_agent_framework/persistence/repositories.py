"""Repositories backed by SQLAlchemy sessions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select, update

from ..schemas import DebateReviewInput
from ..services.jobs import (
    RunSnapshot,
    RunStageEvent,
    RunStageStatus,
    RunStatus,
    resolved_skill_audit,
)
from .database import Database
from .models import (
    AuditLogRecord,
    AuthSessionRecord,
    CanonicalFindingMemberRecord,
    CanonicalFindingRecord,
    HumanReviewRecord,
    ModelCallMetricRecord,
    PaperArtifactRecord,
    PaperAssignmentRecord,
    PaperRecord,
    PaperRevisionRecord,
    ReviewRunRecord,
    ReviewRunStageRecord,
    SourceFindingRecord,
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
        review_fingerprint: str | None = None,
        discipline_id: str | None = None,
        skill_selection_hash: str | None = None,
    ) -> RunSnapshot:
        now = datetime.now(UTC)
        record = ReviewRunRecord(
            task_id=uuid4().hex,
            paper_id=paper_id,
            revision_id=revision_id,
            review_fingerprint=review_fingerprint,
            discipline_id=discipline_id,
            skill_selection_hash=skill_selection_hash,
            status=RunStatus.QUEUED.value,
            current_stage="queued",
            created_at=now,
            updated_at=now,
        )
        with self.database.session() as session:
            session.add(record)
        return self._snapshot(record, [])

    def mark_running(self, task_id: str) -> RunSnapshot:
        return self._update(
            task_id,
            status=RunStatus.RUNNING,
            current_stage="initializing",
            result_json=None,
            error=None,
        )

    def mark_stage(
        self,
        task_id: str,
        *,
        stage: str,
        label: str,
        status: RunStageStatus,
        progress_percent: int,
        detail: str | None = None,
    ) -> RunSnapshot:
        now = datetime.now(UTC)
        with self.database.session() as session:
            record = session.get(ReviewRunRecord, task_id)
            if record is None:
                raise KeyError(task_id)
            stage_record = session.scalar(
                select(ReviewRunStageRecord).where(
                    ReviewRunStageRecord.task_id == task_id,
                    ReviewRunStageRecord.stage == stage,
                )
            )
            if stage_record is None:
                stage_record = ReviewRunStageRecord(
                    id=uuid4().hex,
                    task_id=task_id,
                    stage=stage,
                    label=label,
                    status=status.value,
                    progress_percent=progress_percent,
                    detail=detail,
                    started_at=now,
                )
                session.add(stage_record)
            else:
                stage_record.label = label
                stage_record.status = status.value
                stage_record.progress_percent = progress_percent
                stage_record.detail = detail
            stage_record.completed_at = (
                now if status is not RunStageStatus.RUNNING else None
            )
            record.current_stage = stage
            record.updated_at = now
            session.flush()
            events = self._stage_events(session, task_id)
            return self._snapshot(record, events)

    def mark_succeeded(self, task_id: str, result: dict[str, Any]) -> RunSnapshot:
        audit = resolved_skill_audit(result)
        with self.database.session() as session:
            record = session.get(ReviewRunRecord, task_id)
            if record is None:
                raise KeyError(task_id)
            record.status = RunStatus.SUCCEEDED.value
            record.current_stage = "completed"
            record.result_json = result
            record.error = None
            record.updated_at = datetime.now(UTC)
            record.discipline_id = audit.get("discipline_id") or record.discipline_id
            record.skill_id = audit.get("skill_id")
            record.skill_version = audit.get("skill_version")
            record.skill_profile_hash = audit.get("skill_profile_hash")
            record.skill_versions_json = audit.get("skill_versions", {})
            record.finding_identity_version = result.get("finding_identity_version")
            self._sync_finding_identities(session, task_id, result)
            session.flush()
            return self._snapshot(record, self._stage_events(session, task_id))

    @staticmethod
    def _sync_finding_identities(
        session: Any, task_id: str, result: dict[str, Any]
    ) -> None:
        """Materialize V2 lineage for queries without rewriting legacy runs."""

        session.execute(
            delete(CanonicalFindingRecord).where(
                CanonicalFindingRecord.task_id == task_id
            )
        )

        session.execute(
            delete(SourceFindingRecord).where(SourceFindingRecord.task_id == task_id)
        )
        session.flush()
        if result.get("finding_identity_version") != "finding_identity_v2":
            return

        source_ids: set[str] = set()
        for review in result.get("independent_reviews") or []:
            role = str(review.get("role") or "")
            for finding in review.get("findings") or []:
                finding_id = str(finding.get("finding_id") or "")
                local_ref = str(finding.get("local_ref") or "")
                fingerprint = str(finding.get("fingerprint") or "")
                if not finding_id or not local_ref or not role or not fingerprint:
                    raise ValueError("V2 Source Finding 缺少身份审计字段")
                if finding_id in source_ids:
                    raise ValueError(f"V2 Source Finding ID 重复：{finding_id}")
                source_ids.add(finding_id)
                session.add(
                    SourceFindingRecord(
                        finding_id=finding_id,
                        task_id=task_id,
                        source_role=role,
                        local_ref=local_ref,
                        fingerprint=fingerprint,
                        payload_json=finding,
                    )
                )
        session.flush()

        global_review = ((result.get("synthesis") or {}).get("global_review") or {})
        canonical_ids: set[str] = set()
        member_ids: set[str] = set()
        for finding in global_review.get("resolved_findings") or []:
            canonical_id = str(finding.get("finding_id") or "")
            fingerprint = str(finding.get("fingerprint") or "")
            members = list(finding.get("source_finding_ids") or [])
            if not canonical_id or not fingerprint or not members:
                raise ValueError("V2 Canonical Finding 缺少身份审计字段")
            if canonical_id in canonical_ids:
                raise ValueError(f"V2 Canonical Finding ID 重复：{canonical_id}")
            unknown = sorted(set(members) - source_ids)
            if unknown:
                raise ValueError(f"Canonical Finding 引用了未知 Source：{unknown}")
            duplicate_members = sorted(set(members) & member_ids)
            if duplicate_members:
                raise ValueError(f"Source Finding 被重复归并：{duplicate_members}")
            canonical_ids.add(canonical_id)
            member_ids.update(members)
            session.add(
                CanonicalFindingRecord(
                    finding_id=canonical_id,
                    task_id=task_id,
                    status=str(finding.get("status") or ""),
                    fingerprint=fingerprint,
                    payload_json=finding,
                )
            )
            session.flush()
            for source_id in members:
                session.add(
                    CanonicalFindingMemberRecord(
                        canonical_finding_id=canonical_id,
                        source_finding_id=source_id,
                    )
                )
        if member_ids != source_ids:
            missing = sorted(source_ids - member_ids)
            raise ValueError(f"Canonical Finding 未覆盖全部 Source：{missing}")

    def record_model_call(self, task_id: str, metric: dict[str, Any]) -> None:
        if metric.get("run_id") not in {None, task_id}:
            raise ValueError("模型调用指标的 run_id 与任务不一致")
        with self.database.session() as session:
            if session.get(ReviewRunRecord, task_id) is None:
                raise KeyError(task_id)
            session.add(
                ModelCallMetricRecord(
                    call_id=str(metric["call_id"]),
                    task_id=task_id,
                    node=metric.get("node"),
                    role=metric.get("role"),
                    operation=str(metric.get("operation") or "chat_completion"),
                    model=str(metric.get("model") or "unknown"),
                    prompt_tokens=int(metric.get("prompt_tokens", 0)),
                    cache_hit_tokens=int(metric.get("cache_hit_tokens", 0)),
                    cache_miss_tokens=int(metric.get("cache_miss_tokens", 0)),
                    completion_tokens=int(metric.get("completion_tokens", 0)),
                    reasoning_tokens=int(metric.get("reasoning_tokens", 0)),
                    latency_ms=int(metric.get("latency_ms", 0)),
                    estimated_cost_yuan=float(metric.get("estimated_cost_yuan", 0.0)),
                    prompt_prefix_hash=metric.get("prompt_prefix_hash"),
                    status=str(metric.get("status") or "succeeded"),
                    error=metric.get("error"),
                )
            )
            session.execute(
                update(ReviewRunRecord)
                .where(ReviewRunRecord.task_id == task_id)
                .values(
                    model_call_count=ReviewRunRecord.model_call_count + 1,
                    model_failed_call_count=(
                        ReviewRunRecord.model_failed_call_count
                        + (1 if metric.get("status") == "failed" else 0)
                    ),
                    model_prompt_tokens=ReviewRunRecord.model_prompt_tokens
                    + int(metric.get("prompt_tokens", 0)),
                    model_cache_hit_tokens=ReviewRunRecord.model_cache_hit_tokens
                    + int(metric.get("cache_hit_tokens", 0)),
                    model_cache_miss_tokens=ReviewRunRecord.model_cache_miss_tokens
                    + int(metric.get("cache_miss_tokens", 0)),
                    model_completion_tokens=ReviewRunRecord.model_completion_tokens
                    + int(metric.get("completion_tokens", 0)),
                    model_reasoning_tokens=ReviewRunRecord.model_reasoning_tokens
                    + int(metric.get("reasoning_tokens", 0)),
                    model_estimated_cost_yuan=(
                        ReviewRunRecord.model_estimated_cost_yuan
                        + float(metric.get("estimated_cost_yuan", 0.0))
                    ),
                )
            )

    def mark_resuming(self, task_id: str) -> RunSnapshot:
        """失败重试：恢复为运行中，但保留已完成步骤的进度记录。"""

        now = datetime.now(UTC)
        with self.database.session() as session:
            record = session.get(ReviewRunRecord, task_id)
            if record is None:
                raise KeyError(task_id)
            record.status = RunStatus.RUNNING.value
            record.current_stage = "resuming"
            record.result_json = None
            record.error = None
            record.updated_at = now
            session.flush()
            return self._snapshot(record, self._stage_events(session, task_id))

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
            return (
                self._snapshot(record, self._stage_events(session, task_id))
                if record
                else None
            )

    def list_for_paper(self, paper_id: str) -> list[RunSnapshot]:
        with self.database.session() as session:
            records = session.scalars(
                select(ReviewRunRecord)
                .where(ReviewRunRecord.paper_id == paper_id)
                .order_by(ReviewRunRecord.created_at.desc())
            ).all()
            return [
                self._snapshot(record, self._stage_events(session, record.task_id))
                for record in records
            ]

    def list_recent(self, limit: int = 100) -> list[RunSnapshot]:
        """按更新时间倒序返回最近的任务快照，供任务列表页展示。"""
        with self.database.session() as session:
            records = session.scalars(
                select(ReviewRunRecord)
                .order_by(ReviewRunRecord.updated_at.desc())
                .limit(limit)
            ).all()
            return [
                self._snapshot(record, self._stage_events(session, record.task_id))
                for record in records
            ]

    def find_succeeded_by_fingerprint(
        self, review_fingerprint: str
    ) -> RunSnapshot | None:
        with self.database.session() as session:
            record = session.scalar(
                select(ReviewRunRecord)
                .where(
                    ReviewRunRecord.review_fingerprint == review_fingerprint,
                    ReviewRunRecord.status == RunStatus.SUCCEEDED.value,
                )
                .order_by(ReviewRunRecord.updated_at.desc())
                .limit(1)
            )
            return (
                self._snapshot(record, self._stage_events(session, record.task_id))
                if record
                else None
            )

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
            return self._snapshot(record, self._stage_events(session, task_id))

    @staticmethod
    def _stage_events(session: Any, task_id: str) -> list[ReviewRunStageRecord]:
        return list(
            session.scalars(
                select(ReviewRunStageRecord)
                .where(ReviewRunStageRecord.task_id == task_id)
                .order_by(ReviewRunStageRecord.started_at, ReviewRunStageRecord.stage)
            ).all()
        )

    @staticmethod
    def _snapshot(
        record: ReviewRunRecord,
        stage_records: list[ReviewRunStageRecord],
    ) -> RunSnapshot:
        events = [
            RunStageEvent(
                stage=item.stage,
                label=item.label,
                status=RunStageStatus(item.status),
                progress_percent=item.progress_percent,
                started_at=_aware(item.started_at),
                completed_at=(
                    _aware(item.completed_at) if item.completed_at else None
                ),
                detail=item.detail,
            )
            for item in stage_records
        ]
        current_event = next(
            (item for item in reversed(events) if item.stage == record.current_stage),
            None,
        )
        terminal_progress = 100 if record.status == RunStatus.SUCCEEDED.value else 0
        return RunSnapshot(
            task_id=record.task_id,
            status=RunStatus(record.status),
            created_at=_aware(record.created_at),
            updated_at=_aware(record.updated_at),
            result=record.result_json,
            error=record.error,
            paper_id=record.paper_id,
            revision_id=record.revision_id,
            review_fingerprint=record.review_fingerprint,
            discipline_id=record.discipline_id,
            skill_selection_hash=record.skill_selection_hash,
            skill_id=record.skill_id,
            skill_version=record.skill_version,
            skill_profile_hash=record.skill_profile_hash,
            skill_versions=record.skill_versions_json or {},
            finding_identity_version=record.finding_identity_version,
            model_usage={
                "call_count": record.model_call_count,
                "failed_call_count": record.model_failed_call_count,
                "prompt_tokens": record.model_prompt_tokens,
                "cache_hit_tokens": record.model_cache_hit_tokens,
                "cache_miss_tokens": record.model_cache_miss_tokens,
                "completion_tokens": record.model_completion_tokens,
                "reasoning_tokens": record.model_reasoning_tokens,
                "estimated_cost_yuan": round(record.model_estimated_cost_yuan, 8),
                "cache_hit_rate": (
                    record.model_cache_hit_tokens
                    / max(
                        1,
                        record.model_cache_hit_tokens
                        + record.model_cache_miss_tokens,
                    )
                ),
            },
            current_stage=record.current_stage,
            current_stage_label=(
                current_event.label
                if current_event
                else {
                    "queued": "等待开始",
                    "initializing": "正在初始化评审",
                    "completed": "评审已完成",
                    "failed": "评审失败",
                    "interrupted": "评审已中断",
                }.get(record.current_stage or "")
            ),
            progress_percent=(
                current_event.progress_percent if current_event else terminal_progress
            ),
            stage_started_at=current_event.started_at if current_event else None,
            stage_events=events,
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
        content_sha256: str,
        parent_revision_id: str | None,
        chapter_hashes: dict[str, str],
        change_ratio: float | None,
        change_summary: dict[str, Any],
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
                    content_sha256=content_sha256,
                    parent_revision_id=parent_revision_id,
                    chapter_hashes_json=chapter_hashes,
                    change_ratio=change_ratio,
                    change_summary_json=change_summary,
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
                        "content_sha256": revision.content_sha256,
                        "parent_revision_id": revision.parent_revision_id,
                        "change_ratio": revision.change_ratio,
                        "change_summary": revision.change_summary_json,
                        "mineru_batch_id": revision.mineru_batch_id,
                        "parse_status": revision.parse_status,
                        "created_at": _aware(revision.created_at),
                        "artifact_count": len(revision.artifacts),
                    }
                    for revision in revisions
                ],
            }

    def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            revision = session.get(PaperRevisionRecord, revision_id)
            if revision is None:
                return None
            return {
                "revision_id": revision.id,
                "paper_id": revision.paper_id,
                "sha256": revision.sha256,
                "content_sha256": revision.content_sha256,
                "parent_revision_id": revision.parent_revision_id,
                "chapter_hashes": revision.chapter_hashes_json,
                "change_ratio": revision.change_ratio,
                "change_summary": revision.change_summary_json,
                "pdf_path": revision.pdf_path,
                "structured_input_path": revision.structured_input_path,
                "mineru_batch_id": revision.mineru_batch_id,
                "parse_status": revision.parse_status,
            }

    def find_revision_by_pdf_sha256(self, pdf_sha256: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            revision = session.scalar(
                select(PaperRevisionRecord)
                .where(PaperRevisionRecord.sha256 == pdf_sha256)
                .order_by(PaperRevisionRecord.created_at.desc())
                .limit(1)
            )
            if revision is None:
                return None
            paper = session.get(PaperRecord, revision.paper_id)
            return {
                "revision_id": revision.id,
                "paper_id": revision.paper_id,
                "title": paper.title if paper else "",
                "content_sha256": revision.content_sha256,
                "mineru_batch_id": revision.mineru_batch_id,
            }

    def list_current_revision_candidates(self, title: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.execute(
                select(PaperRecord, PaperRevisionRecord)
                .join(
                    PaperRevisionRecord,
                    PaperRevisionRecord.id == PaperRecord.current_revision_id,
                )
                .where(func.lower(PaperRecord.title) == title.casefold())
            ).all()
            return [
                {
                    "paper_id": paper.id,
                    "revision_id": revision.id,
                    "structured_input_path": revision.structured_input_path,
                }
                for paper, revision in rows
            ]


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

    def pdf_path_for_task(self, task_id: str) -> str | None:
        with self.database.session() as session:
            paper_id = session.scalar(
                select(ReviewRunRecord.paper_id).where(
                    ReviewRunRecord.task_id == task_id
                )
            )
            if not paper_id:
                return None
            paper = session.get(PaperRecord, paper_id)
            if paper is None:
                return None
            revision = session.get(PaperRevisionRecord, paper.current_revision_id)
            return revision.pdf_path if revision else None

    def paper_title_for_task(self, task_id: str) -> str | None:
        with self.database.session() as session:
            paper_id = session.scalar(
                select(ReviewRunRecord.paper_id).where(
                    ReviewRunRecord.task_id == task_id
                )
            )
            if not paper_id:
                return None
            return session.scalar(
                select(PaperRecord.title).where(PaperRecord.id == paper_id)
            )

    def get_published_review_for_paper(self, paper_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            review = session.scalar(
                select(HumanReviewRecord)
                .where(
                    HumanReviewRecord.paper_id == paper_id,
                    HumanReviewRecord.status == "submitted",
                    HumanReviewRecord.published_at.is_not(None),
                )
                .order_by(HumanReviewRecord.published_at.desc())
                .limit(1)
            )
            if review is None:
                return None
            return {
                "review_id": review.id,
                "section_scores": review.section_scores,
                "total_score": review.total_score,
                "advice_content": review.advice_content,
                "submitted_at": (
                    _aware(review.submitted_at) if review.submitted_at else None
                ),
                "published_at": _aware(review.published_at),
            }

    def assign_paper(
        self, *, paper_id: str, reviewer_id: str, assigned_by_id: str
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            if session.get(PaperRecord, paper_id) is None:
                raise KeyError("paper")
            reviewer = session.get(UserRecord, reviewer_id)
            if (
                reviewer is None
                or reviewer.role not in {"teacher", "admin"}
                or not reviewer.is_active
            ):
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

    def list_all_papers_for_teacher(self, reviewer_id: str) -> list[dict[str, Any]]:
        """Return one reviewable assignment per exact PDF across all uploads."""

        now = datetime.now(UTC)
        with self.database.session() as session:
            reviewer = session.get(UserRecord, reviewer_id)
            if (
                reviewer is None
                or reviewer.role not in {"teacher", "admin"}
                or not reviewer.is_active
            ):
                raise KeyError("reviewer")

            papers = session.scalars(
                select(PaperRecord).order_by(PaperRecord.updated_at.desc())
            ).all()
            unique_papers: list[PaperRecord] = []
            seen_hashes: set[str] = set()
            for paper in papers:
                if paper.sha256 in seen_hashes:
                    continue
                seen_hashes.add(paper.sha256)
                unique_papers.append(paper)

            assignments: list[PaperAssignmentRecord] = []
            for paper in unique_papers:
                assignment = session.scalar(
                    select(PaperAssignmentRecord).where(
                        PaperAssignmentRecord.paper_id == paper.id,
                        PaperAssignmentRecord.reviewer_id == reviewer_id,
                    )
                )
                if assignment is None:
                    assignment = PaperAssignmentRecord(
                        id=uuid4().hex,
                        paper_id=paper.id,
                        reviewer_id=reviewer_id,
                        assigned_by_id=reviewer_id,
                        status="assigned",
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(assignment)
                    session.flush()
                    self._audit(
                        session,
                        actor_id=reviewer_id,
                        action="paper.auto_assigned",
                        resource_type="assignment",
                        resource_id=assignment.id,
                        details={"paper_id": paper.id, "source": "teacher_inbox"},
                    )
                assignments.append(assignment)

            return [
                self._assignment_dict(session, assignment)
                for assignment in assignments
            ]

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

    def publish_human_review(
        self, *, review_id: str, published_by_id: str
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            review = session.get(HumanReviewRecord, review_id)
            if review is None:
                raise KeyError("review")
            if review.status != "submitted":
                raise ValueError("只有已提交的终审可以发布")
            review.published_at = now
            review.published_by_id = published_by_id
            review.updated_at = now
            self._audit(
                session,
                actor_id=published_by_id,
                action="review.published",
                resource_type="human_review",
                resource_id=review.id,
                details={"paper_id": review.paper_id},
            )
            session.flush()
            return self._review_dict(review)

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
            "ai_section_scores": self._ai_section_scores(
                latest_run.result_json if latest_run else None
            ),
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
            "published_at": _aware(record.published_at) if record.published_at else None,
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
    def _ai_section_scores(result: dict[str, Any] | None) -> list[int] | None:
        if not result:
            return None
        values = result.get("final_score", {}).get("legacy_level_scores")
        if (
            not isinstance(values, list)
            or len(values) != 18
            or any(not isinstance(value, int) or value not in {0, 1, 2, 3} for value in values)
        ):
            return None
        return values

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
