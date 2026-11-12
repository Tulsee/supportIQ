"""Analytics responses (Module 7)."""

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
