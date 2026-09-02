"""SQLAlchemy database bootstrap and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


class Database:
    def __init__(self, url: str) -> None:
        parsed_url = make_url(url)
        if parsed_url.get_backend_name() == "sqlite" and parsed_url.database not in {
            None,
            "",
            ":memory:",
        }:
            Path(parsed_url.database).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        connect_args = (
            {"check_same_thread": False, "timeout": 30}
            if url.startswith("sqlite")
            else {}
        )
        self.engine: Engine = create_engine(url, connect_args=connect_args)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._configure_sqlite)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def migrate(self) -> None:
        from alembic import command
        from alembic.config import Config

        repository_root = Path(__file__).resolve().parents[4]
        config = Config()
        config.set_main_option(
            "script_location", str(repository_root / "backend" / "migrations")
        )
        config.set_main_option(
            "sqlalchemy.url", str(self.engine.url).replace("%", "%%")
        )
        legacy_revision = self._infer_unversioned_revision()
        if legacy_revision is not None:
            command.stamp(config, legacy_revision)
        command.upgrade(config, "head")

    def _infer_unversioned_revision(self) -> str | None:
        inspector = inspect(self.engine)
        tables = set(inspector.get_table_names())
        if "papers" not in tables or "review_runs" not in tables:
            return None
        if "alembic_version" in tables:
            with self.engine.connect() as connection:
                if connection.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                ).scalar_one_or_none():
                    return None
        review_run_columns = {
            item["name"] for item in inspector.get_columns("review_runs")
        }
        has_skill_prerequisites = {
            "users", "human_reviews", "review_run_stages"
        }.issubset(tables)
        if "model_call_metrics" in tables and {
            "model_call_count",
            "model_prompt_tokens",
            "model_cache_hit_tokens",
            "model_cache_miss_tokens",
            "model_completion_tokens",
            "model_reasoning_tokens",
            "model_estimated_cost_yuan",
        }.issubset(review_run_columns):
            return "20260828_0010"
        if {
            "source_findings", "canonical_findings", "canonical_finding_members"
        }.issubset(tables) and "finding_identity_version" in review_run_columns:
            return "20260828_0009"
        if has_skill_prerequisites and {
            "skill_id", "skill_version", "skill_profile_hash", "skill_versions_json"
        }.issubset(review_run_columns):
            return "20260825_0008"
        if has_skill_prerequisites and {
            "discipline_id", "skill_selection_hash"
        }.issubset(review_run_columns):
            return "20260825_0007"
        if "student_task_access" in tables:
            return "20260819_0003"
        if "users" in tables and "human_reviews" in tables:
            return "20260819_0002"
        return "20260819_0001"

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _configure_sqlite(  # type: ignore[no-untyped-def]
        dbapi_connection, _connection_record
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
