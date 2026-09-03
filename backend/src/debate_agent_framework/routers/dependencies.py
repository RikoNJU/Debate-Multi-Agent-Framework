"""FastAPI dependencies backed by application lifespan state."""

from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..services.paper_storage import PaperPersistenceService
from ..services.workflow_service import DebateWorkflowService
from ..persistence import PortalRepository
from ..aigc import AigcDetectionService


bearer_scheme = HTTPBearer(auto_error=False)


def get_debate_workflow_service(request: Request) -> DebateWorkflowService:
    return request.app.state.workflow_service


def get_paper_persistence_service(request: Request) -> PaperPersistenceService:
    return request.app.state.paper_persistence_service


def get_portal_repository(request: Request) -> PortalRepository:
    return request.app.state.portal_repository


def get_aigc_detection_service(request: Request) -> AigcDetectionService:
    return request.app.state.aigc_detection_service


def get_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def get_current_user(
    token: str = Depends(get_bearer_token),
    repository: PortalRepository = Depends(get_portal_repository),
) -> dict[str, Any]:
    user = repository.get_user_for_token(token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_roles(*roles: str) -> Callable[..., dict[str, Any]]:
    def dependency(
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return user

    return dependency
