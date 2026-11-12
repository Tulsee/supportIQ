"""Analytics responses (Module 7)."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class TicketTotals(BaseModel):
    open: int
    unassigned: int = Field(description="Open and nobody assigned.")
    urgent: int = Field(description="Open with urgent priority.")
    created: int = Field(description="Opened in the period.")
    resolved: int = Field(description="Resolved in the period.")


class DailyTickets(BaseModel):
    day: str
    created: int
    resolved: int


class SlaCompliance(BaseModel):
    on_time: int = Field(description="Met by the deadline.")
    late: int = Field(description="Met after the deadline.")
    overdue: int = Field(description="Not met and past the deadline.")
    open: int = Field(description="Not met yet, within the deadline (not in the rate).")
    cancelled: int = Field(description="Closed unresolved or no longer covered (not in the rate).")
    rate: float | None = Field(description="on_time / (on_time + late + overdue).")


class SlaSummary(BaseModel):
    """Clocks started in the period (first cycles)."""

    configured: bool = Field(description="Service levels are set up.")
    first_response: SlaCompliance
    resolution: SlaCompliance
    reopened_cycles: int = Field(description="Resolution cycles after a reopen, counted apart.")


class Overview(BaseModel):
    days: int
    tickets: TicketTotals
    open_by_status: dict[str, int]
    daily: list[DailyTickets]
    median_first_response_minutes: float | None = Field(
        description="From opening to the first public agent reply, for tickets opened in "
        "the period that got one."
    )
    tickets_replied: int
    median_resolution_minutes: float | None = Field(
        description="From opening to resolution, for tickets resolved in the period."
    )
    customers: int
    indexed_documents: int
    sla: SlaSummary


class AnswerStats(BaseModel):
    total: int = Field(description="Questions and reply suggestions in the period.")
    by_status: dict[str, int]
    by_kind: dict[str, dict[str, int]]
    answer_rate: float | None = Field(description="Share answered (with or without citations).")
    human_review_rate: float | None = Field(
        description="Share escalated, abstained or failed: handed to a person."
    )


class DecisionStat(BaseModel):
    decision: str
    reason: str
    count: int
    mean_score: float | None = Field(description="Mean evidence score (not a probability).")


class LatencyStat(BaseModel):
    p50_ms: float
    p95_ms: float
    runs: int


class FeedbackStats(BaseModel):
    helpful: int
    not_helpful: int
    helpful_rate: float | None


class ModelUsage(BaseModel):
    provider: str
    model: str
    runs: int
    input_tokens: int
    output_tokens: int


class KnowledgeGap(BaseModel):
    query: str
    count: int = Field(description="Times asked without a confident answer.")
    last_asked: str
    run_id: str = Field(description="The latest run, to inspect.")
    status: str


class AIPerformance(BaseModel):
    days: int
    daily_runs: list[dict[str, int | str]] = Field(
        description="Per day: {day, retrieval, answer, suggestion, summary} run counts."
    )
    answers: AnswerStats
    decisions: list[DecisionStat]
    latency: dict[str, LatencyStat] = Field(description="By run kind.")
    feedback: FeedbackStats
    models: list[ModelUsage]
    knowledge_gaps: list[KnowledgeGap] = Field(
        description="Questions most often not answered confidently: what the knowledge base "
        "is missing or gets wrong."
    )


# --- Routing (Module 10) ------------------------------------------------------------------


class RoutingOutcomes(BaseModel):
    assigned: int
    team_only: int = Field(description="The rule's strategy is manual: the team picks it up.")
    no_agent: int = Field(description="A rule matched, but nobody in the team could take it.")
    no_rule: int = Field(description="No rule matched.")


class RoutingDecisionStats(BaseModel):
    total: int = Field(description="Decisions carried out in the period (including reroutes).")
    by_outcome: RoutingOutcomes
    shadow_total: int = Field(description="Decisions recorded in shadow mode, not carried out.")
    shadow_by_outcome: RoutingOutcomes


class AssignmentStats(BaseModel):
    """The first assignment of each ticket opened in the period."""

    tickets_assigned: int
    automatic: int = Field(description="By routing (including reroutes a person asked for).")
    manual: int = Field(description="By a person, including when they opened the ticket.")
    automatic_rate: float | None
    median_minutes: float | None = Field(description="From opening to the first assignment.")
    median_minutes_automatic: float | None
    median_minutes_manual: float | None


class OverrideStats(BaseModel):
    automatic: int = Field(description="Automatic assignments made in the period.")
    changed_within_hour: int = Field(
        description="Of those, reassigned or unassigned by a person within an hour."
    )
    rate: float | None


class ShadowStats(BaseModel):
    decisions: int = Field(description="Shadow decisions that picked an agent.")
    compared: int = Field(description="Of those, tickets a person then assigned.")
    agreed: int = Field(description="The person picked the same agent.")
    rate: float | None = Field(description="agreed / compared.")


class AgentWorkload(BaseModel):
    user_id: uuid.UUID
    name: str
    active_tickets: int = Field(description="Now: assigned to them, assigned or in progress.")
    max_active_tickets: int
    assigned_automatically: int = Field(description="In the period.")
    assigned_by_people: int = Field(description="In the period.")


class RoutingAnalytics(BaseModel):
    days: int
    mode: Literal["off", "shadow", "on"] = Field(description="The routing mode now.")
    decisions: RoutingDecisionStats
    assignment: AssignmentStats
    overrides: OverrideStats
    shadow: ShadowStats
    workload: list[AgentWorkload] = Field(description="Every admin and agent, by name.")
