"""Local SQLite and filesystem persistence tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import inspect

from debate_agent_framework.persistence import (
    Database,
    PaperRepository,
    SqlAlchemyRunStore,
)
from debate_agent_framework.persistence.models import (
    Base,
    PaperArtifactRecord,
    PaperRecord,
    PaperRevisionRecord,
    ReviewRunRecord,
)
from debate_agent_framework.schemas import (
    ChapterInput,
    DebateReviewInput,
    MinerUParseResult,
    PaperType,
)
from debate_agent_framework.services.jobs import RunStageStatus, RunStatus
from debate_agent_framework.services.paper_storage import PaperPersistenceService


def database_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def review_input(*, paper_id: str = "paper-persistence") -> DebateReviewInput:
    return DebateReviewInput(
        paper_id=paper_id,
        title="持久化测试论文",
        full_text="第一章介绍持久化测试。",
        paper_type=PaperType.ENGINEERING,
        chapters=[
            ChapterInput(
                chapter_id="C1",
                chapter_name="第一章 绪论",
                stage="引言/绪论",
                content="第一章介绍持久化测试。",
            )
        ],
    )


def test_run_and_result_survive_database_reopen(tmp_path: Path) -> None:
    url = database_url(tmp_path / "runs.db")
    first = Database(url)
    first.create_schema()
    first_store = SqlAlchemyRunStore(first)
    created = first_store.create(paper_id="P1", revision_id="R1")
    first_store.mark_running(created.task_id)
    first_store.mark_succeeded(created.task_id, {"final_score": {"total_score": 88}})
    first.dispose()

    reopened = Database(url)
    reopened.create_schema()
    restored = SqlAlchemyRunStore(reopened).get(created.task_id)
    reopened.dispose()

    assert restored is not None
    assert restored.status is RunStatus.SUCCEEDED
    assert restored.paper_id == "P1"
    assert restored.revision_id == "R1"
    assert restored.result == {"final_score": {"total_score": 88}}


def test_run_stage_progress_survives_database_reopen(tmp_path: Path) -> None:
    url = database_url(tmp_path / "progress.db")
    database = Database(url)
    database.create_schema()
    store = SqlAlchemyRunStore(database)
    created = store.create()
    store.mark_running(created.task_id)
    store.mark_stage(
        created.task_id,
        stage="independent_review",
        label="三位专家并行初审",
        status=RunStageStatus.RUNNING,
        progress_percent=25,
    )
    store.mark_stage(
        created.task_id,
        stage="independent_review",
        label="三位专家并行初审",
        status=RunStageStatus.SUCCEEDED,
        progress_percent=55,
    )
    database.dispose()

    reopened = Database(url)
    restored = SqlAlchemyRunStore(reopened).get(created.task_id)
    reopened.dispose()

    assert restored is not None
    assert restored.current_stage_label == "三位专家并行初审"
    assert restored.progress_percent == 55
    assert len(restored.stage_events) == 1
    assert restored.stage_events[0].status is RunStageStatus.SUCCEEDED
    assert restored.stage_events[0].completed_at is not None


def test_running_task_is_marked_interrupted_after_restart(tmp_path: Path) -> None:
    database = Database(database_url(tmp_path / "interrupted.db"))
    database.create_schema()
    store = SqlAlchemyRunStore(database)
    created = store.create()
    store.mark_running(created.task_id)

    assert store.mark_interrupted() == 1
    restored = store.get(created.task_id)
    database.dispose()

    assert restored is not None
    assert restored.status is RunStatus.INTERRUPTED
    assert restored.current_stage == "interrupted"
    assert "服务重启" in (restored.error or "")


def test_migrate_adopts_unversioned_legacy_database(tmp_path: Path) -> None:
    database = Database(database_url(tmp_path / "legacy.db"))
    Base.metadata.create_all(
        database.engine,
        tables=[
            PaperRecord.__table__,
            PaperRevisionRecord.__table__,
            PaperArtifactRecord.__table__,
            ReviewRunRecord.__table__,
        ],
    )

    database.migrate()
    inspector = inspect(database.engine)
    with database.engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    tables = set(inspector.get_table_names())
    database.dispose()

    assert revision == "20260903_0012"
    assert {"users", "review_run_stages"}.issubset(tables)
    assert "student_task_access" not in tables
    assert "aigc_detection_tasks" in tables


def test_resolved_skill_audit_is_persisted(tmp_path: Path) -> None:
    database = Database(database_url(tmp_path / "skill-audit.db"))
    database.create_schema()
    store = SqlAlchemyRunStore(database)
    created = store.create(discipline_id="artificial_intelligence")
    profile_hash = "a" * 64

    saved = store.mark_succeeded(
        created.task_id,
        {
            "review_profile": {
                "skill_id": "ai.method.v2",
                "discipline_id": "artificial_intelligence",
                "version": "2.0.0",
                "profile_hash": profile_hash,
                "base_version": "1.0.0",
                "discipline_version": "1.1.0",
                "classification_version": "ai_method_classification_v2",
                "rubric_version": "ai_method_rubric_v2",
                "score_schema_id": "legacy_18_dimensions_v1",
            }
        },
    )
    database.dispose()

    assert saved.skill_id == "ai.method.v2"
    assert saved.skill_version == "2.0.0"
    assert saved.skill_profile_hash == profile_hash
    assert saved.skill_versions["discipline_version"] == "1.1.0"


def test_paper_files_and_artifacts_are_archived_safely(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    database = Database(database_url(data_dir / "debate.db"))
    database.create_schema()
    repository = PaperRepository(database)
    storage = PaperPersistenceService(data_dir, repository)

    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.7\nlocal persistence")
    mineru_dir = tmp_path / "mineru-output"
    mineru_dir.mkdir()
    markdown_path = mineru_dir / "full.md"
    markdown_path.write_text("# 持久化测试论文", encoding="utf-8")
    content_list_path = mineru_dir / "content_list.json"
    content_list_path.write_text(
        json.dumps([{"type": "text", "text": "测试"}], ensure_ascii=False),
        encoding="utf-8",
    )
    parsed = MinerUParseResult(
        batch_id="batch-persistence",
        markdown=markdown_path.read_text(encoding="utf-8"),
        output_dir=str(mineru_dir),
        markdown_path=str(markdown_path),
        content_list_path=str(content_list_path),
        artifacts=["full.md", "content_list.json"],
    )

    first_revision = storage.persist(
        review_input=review_input(paper_id="../../unsafe-paper-id"),
        parsed=parsed,
        source_pdf=source_pdf,
        source_filename="thesis.pdf",
    )
    persisted = storage.persist(
        review_input=review_input(paper_id="../../unsafe-paper-id"),
        parsed=parsed,
        source_pdf=source_pdf,
        source_filename="thesis-v2.pdf",
    )
    paper = repository.get_paper("../../unsafe-paper-id")
    database.dispose()

    assert persisted.revision_dir.is_relative_to(data_dir / "papers")
    assert (persisted.revision_dir / "source.pdf").read_bytes().startswith(b"%PDF-")
    assert (persisted.revision_dir / "structured_input.json").is_file()
    assert (persisted.revision_dir / "content_list.json").is_file()
    assert paper is not None
    assert paper["current_revision_id"] == persisted.revision_id
    assert first_revision.revision_id != persisted.revision_id
    assert len(paper["revisions"]) == 2
    assert paper["revisions"][0]["artifact_count"] >= 5
    stored_pdf_path = (persisted.revision_dir / "source.pdf").relative_to(data_dir)
    assert storage.resolve_stored_path(stored_pdf_path).is_file()
    restored_input = storage.load_review_input(persisted.revision_id)
    assert restored_input.paper_id == "../../unsafe-paper-id"
    assert restored_input.title == "持久化测试论文"
    with pytest.raises(ValueError, match="超出"):
        storage.resolve_stored_path("../../outside.pdf")
