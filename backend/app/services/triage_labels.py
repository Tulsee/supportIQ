"""What a person's category choice means for triage (Module 11): the ticket becomes,
or stops being, an example for the neighbour vote, and the ticket's prediction (if
there is one and nobody reviewed it yet) records whether the person agreed.

Called by ``TicketService`` in the same transaction as the change, only for changes
a person makes: categories the AI applied are never examples, so the neighbour vote
can't learn from its own mistakes.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utcnow
from app.models import Ticket, TicketCategory, TicketTriage, TriageLabel, TriageOutcome


async def record_category_choice(
    session: AsyncSession, ticket: Ticket, category: TicketCategory | None, actor_id: uuid.UUID
) -> None:
    label = await session.get(TriageLabel, ticket.id)
    if category is None:
        if label is not None:
            await session.delete(label)
    elif label is None:
        session.add(
            TriageLabel(
                ticket_id=ticket.id,
                organization_id=ticket.organization_id,
                category_key=category.key,
            )
        )
    else:
        label.category_key = category.key  # the embedding (of the ticket's text) stays

    triage = await session.scalar(select(TicketTriage).where(TicketTriage.ticket_id == ticket.id))
    if triage is None or triage.reviewed_at is not None:
        return
    triage.reviewed_at = utcnow()
    triage.reviewed_by_id = actor_id
    triage.final_category_key = category.key if category else None
    if category is None:
        triage.outcome = TriageOutcome.CLEARED
    elif category.key == triage.category_key:
        triage.outcome = TriageOutcome.CONFIRMED
    else:
        triage.outcome = TriageOutcome.CHANGED
