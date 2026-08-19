"""FastAPI dependencies backed by application lifespan state."""

from fastapi import Request

from ..services.paper_storage import PaperPersistenceService
from ..services.workflow_service import DebateWorkflowService


def get_debate_workflow_service(request: Request) -> DebateWorkflowService:
    return request.app.state.workflow_service


def get_paper_persistence_service(request: Request) -> PaperPersistenceService:
    return request.app.state.paper_persistence_service
