from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.common import error_responses
from app.schemas.users import UserRead, UserUpdate
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"], responses=error_responses(401, 422))


@router.get("/me", summary="Current user's profile")
async def read_current_user(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch("/me", summary="Update current user's profile")
async def update_current_user(body: UserUpdate, user: CurrentUser, session: DbSession) -> UserRead:
    updated = await UserService(session).update_profile(user, body)
    return UserRead.model_validate(updated)
