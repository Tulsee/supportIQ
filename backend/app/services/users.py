from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.schemas.users import UserUpdate


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def update_profile(self, user: User, data: UserUpdate) -> User:
        user.full_name = data.full_name
        await self.session.commit()
        return user
