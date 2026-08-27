"""Debate 论文评审 Web 应用配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class DebateWebSettings:
    app_name: str = "Debate 论文评审 Multi-Agent"
    api_prefix: str = "/api/debate"
    host: str = "0.0.0.0"
    port: int = 8020
    runtime: str = "demo"
    data_dir: str = "backend/data"
    database_url: str | None = None
    mineru_output_dir: str = "backend/data/mineru"
    portal_session_hours: int = 168
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_display_name: str = "系统管理员"
    cors_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:5174",
    )
    tectonic_path: str | None = None

    @classmethod
    def from_env(cls) -> "DebateWebSettings":
        origins = os.getenv("DEBATE_CORS_ORIGINS")
        return cls(
            host=os.getenv("DEBATE_HOST", cls.host),
            port=int(os.getenv("DEBATE_PORT", str(cls.port))),
            runtime=os.getenv("DEBATE_RUNTIME", cls.runtime),
            data_dir=os.getenv("DEBATE_DATA_DIR", cls.data_dir),
            database_url=os.getenv("DEBATE_DATABASE_URL") or None,
            mineru_output_dir=os.getenv(
                "DEBATE_MINERU_OUTPUT_DIR", cls.mineru_output_dir
            ),
            portal_session_hours=int(
                os.getenv("DEBATE_PORTAL_SESSION_HOURS", str(cls.portal_session_hours))
            ),
            bootstrap_admin_username=os.getenv("DEBATE_BOOTSTRAP_ADMIN_USERNAME") or None,
            bootstrap_admin_password=os.getenv("DEBATE_BOOTSTRAP_ADMIN_PASSWORD") or None,
            bootstrap_admin_display_name=os.getenv(
                "DEBATE_BOOTSTRAP_ADMIN_DISPLAY_NAME", cls.bootstrap_admin_display_name
            ),
            cors_origins=(
                tuple(item.strip() for item in origins.split(",") if item.strip())
                if origins
                else cls.cors_origins
            ),
            tectonic_path=os.getenv("DEBATE_TECTONIC_PATH") or None,
        )

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        database_path = self.resolved_data_dir() / "debate.db"
        return f"sqlite:///{database_path.as_posix()}"

    def resolved_data_dir(self) -> Path:
        """Resolve relative storage paths against the repository, not the shell CWD."""
        configured = Path(self.data_dir).expanduser()
        if configured.is_absolute():
            return configured.resolve()
        return (_REPOSITORY_ROOT / configured).resolve()
