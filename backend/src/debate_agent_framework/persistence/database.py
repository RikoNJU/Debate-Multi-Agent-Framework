"""SQLAlchemy database bootstrap and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
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
        command.upgrade(config, "head")

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
