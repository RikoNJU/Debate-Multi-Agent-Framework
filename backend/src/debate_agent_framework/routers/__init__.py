from .health import router as health_router
from .papers import router as papers_router
from .runs import router as runs_router

__all__ = ["health_router", "papers_router", "runs_router"]
