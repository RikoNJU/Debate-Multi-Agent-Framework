"""Portal authentication endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..persistence import PortalRepository
from ..schemas import LoginRequest, LoginResponse, UserResponse
from .dependencies import (
    get_bearer_token,
    get_current_user,
    get_portal_repository,
)

router = APIRouter(prefix="/portal/auth", tags=["portal-auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    repository: PortalRepository = Depends(get_portal_repository),
) -> LoginResponse:
    user = repository.authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token, expires_at = repository.create_session(user["id"])
    return LoginResponse(
        access_token=token,
        expires_at=expires_at,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def me(user: dict[str, Any] = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(user)


@router.post("/logout", status_code=204)
async def logout(
    token: str = Depends(get_bearer_token),
    repository: PortalRepository = Depends(get_portal_repository),
) -> None:
    repository.revoke_session(token)
