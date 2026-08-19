"""Debate 论文评审 Web 应用配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    cors_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:5174",
    )

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
            cors_origins=(
                tuple(item.strip() for item in origins.split(",") if item.strip())
                if origins
                else cls.cors_origins
            ),
        )

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        database_path = (Path(self.data_dir) / "debate.db").resolve()
        return f"sqlite:///{database_path.as_posix()}"
