from .admin import router as admin_router
from .auth import router as auth_router
from .health import router as health_router
from .papers import router as papers_router
from .runs import router as runs_router
from .student import router as student_router
from .teacher import router as teacher_router

__all__ = [
    "admin_router",
    "auth_router",
    "health_router",
    "papers_router",
    "runs_router",
    "student_router",
    "teacher_router",
]
