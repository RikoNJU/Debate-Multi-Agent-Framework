"""Debate 论文评审 FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.env.loadenv import load_env_file

from .config import DebateWebSettings
from .persistence import Database, PaperRepository, PortalRepository, SqlAlchemyRunStore
from .routers import (
    admin_router,
    auth_router,
    health_router,
    papers_router,
    runs_router,
    student_router,
    teacher_router,
)
from .services import DebateWorkflowService
from .services.paper_storage import PaperPersistenceService


load_env_file(Path(__file__).resolve().parent.parent.parent / ".env")


def create_app(settings: DebateWebSettings | None = None) -> FastAPI:
    settings = settings or DebateWebSettings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):  # type: ignore[no-untyped-def]
        data_dir = Path(settings.data_dir).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        database = Database(settings.resolved_database_url())
        database.migrate()
        run_store = SqlAlchemyRunStore(database)
        run_store.mark_interrupted()
        paper_repository = PaperRepository(database)
        portal_repository = PortalRepository(
            database, session_hours=settings.portal_session_hours
        )
        portal_repository.ensure_bootstrap_admin(
            settings.bootstrap_admin_username,
            settings.bootstrap_admin_password,
            settings.bootstrap_admin_display_name,
        )
        application.state.database = database
        application.state.run_store = run_store
        application.state.paper_repository = paper_repository
        application.state.portal_repository = portal_repository
        application.state.paper_persistence_service = PaperPersistenceService(
            data_dir, paper_repository
        )
        application.state.workflow_service = DebateWorkflowService(
            store=run_store,
            runtime=settings.runtime,
        )
        try:
            yield
        finally:
            database.dispose()

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Evidence-grounded debate review workflow API",
        lifespan=lifespan,
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
    application.include_router(auth_router, prefix=settings.api_prefix)
    application.include_router(teacher_router, prefix=settings.api_prefix)
    application.include_router(admin_router, prefix=settings.api_prefix)
    application.include_router(student_router, prefix=settings.api_prefix)
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
