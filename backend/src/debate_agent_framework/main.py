"""Debate 论文评审 FastAPI 应用入口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.env.loadenv import load_env_file

from .config import DebateWebSettings
from .routers import health_router, papers_router, runs_router


load_env_file(Path(__file__).resolve().parent.parent.parent / ".env")


def create_app(settings: DebateWebSettings | None = None) -> FastAPI:
    settings = settings or DebateWebSettings.from_env()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Evidence-grounded debate review workflow API",
    )
    application.state.settings = settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health_router, prefix=settings.api_prefix)
    application.include_router(papers_router, prefix=settings.api_prefix)
    application.include_router(runs_router, prefix=settings.api_prefix)
    return application


app = create_app()


def run() -> None:
    import uvicorn

    settings = DebateWebSettings.from_env()
    uvicorn.run(
        "debate_agent_framework.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
