import uuid

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(select(User).where(User.email == email.lower()))

    def add(self, user: User) -> None:
        self.session.add(user)

    async def list_all(
        self, *, search: str | None, limit: int, offset: int
    ) -> tuple[list[User], int]:
        condition = (
            User.email.icontains(search, autoescape=True)
            | User.full_name.icontains(search, autoescape=True)
            if search
            else true()
        )
        stmt = select(User).where(condition).order_by(User.email).limit(limit).offset(offset)
        users = list((await self.session.scalars(stmt)).all())
        total = await self.session.scalar(select(func.count()).select_from(User).where(condition))
        return users, total or 0
