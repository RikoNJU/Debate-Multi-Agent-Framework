"""Debate 论文评审 Web 应用配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DebateWebSettings:
    app_name: str = "Debate 论文评审 Multi-Agent"
    api_prefix: str = "/api/debate"
    host: str = "0.0.0.0"
    port: int = 8020
    mineru_output_dir: str = "backend/src/debate_agent_framework/data/mineru"
    cors_origins: tuple[str, ...] = (
        "http://localhost:3001",
        "http://localhost:5174",
    )

    @classmethod
    def from_env(cls) -> "DebateWebSettings":
        origins = os.getenv("DEBATE_CORS_ORIGINS")
        return cls(
            host=os.getenv("DEBATE_HOST", cls.host),
            port=int(os.getenv("DEBATE_PORT", str(cls.port))),
            mineru_output_dir=os.getenv(
                "DEBATE_MINERU_OUTPUT_DIR", cls.mineru_output_dir
            ),
            cors_origins=(
                tuple(item.strip() for item in origins.split(",") if item.strip())
                if origins
                else cls.cors_origins
            ),
        )
