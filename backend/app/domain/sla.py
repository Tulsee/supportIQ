"""SLA clock states as people see them, derived from a clock's stored fields and the
current time (pure, so it can't drift from the deadline arithmetic)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Status = Literal["on_track", "at_risk", "breached", "paused", "met", "met_late", "cancelled"]


@dataclass(frozen=True)
class ClockTimes:
    state: str  # stored: running | paused | met | cancelled
    warn_at: datetime
    due_at: datetime
    met_at: datetime | None
    breached_at: datetime | None


def status(clock: ClockTimes, now: datetime) -> Status:
    """``met_late``: the target was met after its deadline (the breach stays on record).
    A paused clock that had already run out shows as breached; otherwise as paused."""
    if clock.state == "cancelled":
        return "cancelled"
    if clock.met_at is not None:
        return "met_late" if clock.breached_at or clock.met_at > clock.due_at else "met"
    if clock.breached_at is not None:
        return "breached"
    if clock.state == "paused":  # not running, so it can't run out
        return "paused"
    if now >= clock.due_at:
        return "breached"
    return "at_risk" if now >= clock.warn_at else "on_track"
