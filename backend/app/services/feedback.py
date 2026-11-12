"""Staff ratings of AI runs: helpful or not, with an optional comment. One rating per
person and run (rating again replaces it). Analytics report the helpful rate."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models import AIFeedback, AIRun, FeedbackRating, OrganizationMember, User
from app.services.authorization import STAFF_ROLES, require_role


class FeedbackService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _run(self, member: OrganizationMember, run_id: uuid.UUID) -> AIRun:
        require_role(member, *STAFF_ROLES)
        run = await self.session.scalar(
            select(AIRun).where(AIRun.id == run_id, AIRun.organization_id == member.organization_id)
        )
        if run is None:
            raise NotFoundError("Run not found")
        return run

    async def rate(
        self,
        member: OrganizationMember,
        user: User,
        run_id: uuid.UUID,
        rating: FeedbackRating,
        comment: str | None,
    ) -> dict[str, object]:
        run = await self._run(member, run_id)
        feedback = await self.session.scalar(
            select(AIFeedback).where(AIFeedback.ai_run_id == run.id, AIFeedback.user_id == user.id)
        )
        if feedback is None:
            feedback = AIFeedback(
                organization_id=run.organization_id, ai_run_id=run.id, user_id=user.id
            )
            self.session.add(feedback)
        feedback.rating = rating
        feedback.comment = comment or None
        await self.session.commit()
        return await self._summary(run.id, user.id)

    async def summary(
        self, member: OrganizationMember, user: User, run_id: uuid.UUID
    ) -> dict[str, object]:
        run = await self._run(member, run_id)
        return await self._summary(run.id, user.id)

    async def _summary(self, run_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, object]:
        counts = dict(
            (
                await self.session.execute(
                    select(AIFeedback.rating, func.count())
                    .where(AIFeedback.ai_run_id == run_id)
                    .group_by(AIFeedback.rating)
                )
            ).all()
        )
        mine = await self.session.scalar(
            select(AIFeedback).where(AIFeedback.ai_run_id == run_id, AIFeedback.user_id == user_id)
        )
        return {
            "helpful": counts.get(FeedbackRating.HELPFUL, 0),
            "not_helpful": counts.get(FeedbackRating.NOT_HELPFUL, 0),
            "mine": mine.rating if mine else None,
            "my_comment": mine.comment if mine else None,
        }
