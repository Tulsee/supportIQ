"""Routing rules, the simulator and routing decisions (Module 10, phase 6)."""

import re
import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.domain.routing import CUSTOMER_OPERATORS, CUSTOMER_PREFIX, FIELD_OPERATORS, Operator
from app.models import (
    RoutingDecision,
    RoutingOutcome,
    RoutingRule,
    RoutingStrategy,
    RoutingTrigger,
    TicketPriority,
    User,
)
from app.schemas.teams import MAX_SKILLS, SkillLevel
from app.schemas.tickets import PersonSummary

RuleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
_FIELD = re.compile(r"^(category|priority|subject|customer\.[a-z0-9_]{1,50})$")
MAX_CONDITIONS = 20
MAX_VALUES = 50


class ConditionIn(BaseModel):
    """One test on the ticket. Fields: `category` (by key: eq, in, is_set),
    `priority` (eq, in), `subject` (contains), and `customer.<key>` for the customer's
    metadata (eq, in, contains, is_set). Text comparisons ignore case."""

    field: str = Field(
        description="`category`, `priority`, `subject` or `customer.<key>`.",
        examples=["category", "customer.plan"],
    )
    operator: Operator
    value: Text | list[Text] | bool = Field(
        description="Text for eq and contains, a list for in, true or false for is_set."
    )

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if not _FIELD.match(self.field):
            raise ValueError("field must be category, priority, subject or customer.<key>")
        allowed = (
            CUSTOMER_OPERATORS
            if self.field.startswith(CUSTOMER_PREFIX)
            else FIELD_OPERATORS[self.field]
        )
        if self.operator not in allowed:
            names = ", ".join(sorted(allowed))
            raise ValueError(f"{self.field} takes these operators: {names}")
        value = self.value
        if self.operator == Operator.IS_SET:
            if not isinstance(value, bool):
                raise ValueError("is_set takes true or false")
        elif self.operator == Operator.IN:
            if not isinstance(value, list) or not 1 <= len(value) <= MAX_VALUES:
                raise ValueError(f"in takes a list of 1 to {MAX_VALUES} values")
        elif not isinstance(value, str):
            raise ValueError(f"{self.operator} takes one text value")
        if self.field in ("priority", "category") and self.operator != Operator.IS_SET:
            values = value if isinstance(value, list) else [value]
            normalized = [str(v).lower() for v in values]
            if self.field == "priority":
                unknown = [v for v in normalized if v not in set(TicketPriority)]
                if unknown:
                    raise ValueError(f"unknown priorities: {', '.join(unknown)}")
            self.value = normalized if isinstance(value, list) else normalized[0]
        return self


class Conditions(BaseModel):
    """Every `all` condition must hold, and one of `any` (when there are any). No
    conditions: the rule matches every ticket."""

    all: list[ConditionIn] = Field(default_factory=list, max_length=MAX_CONDITIONS)
    any: list[ConditionIn] = Field(default_factory=list, max_length=MAX_CONDITIONS)


def _distinct_skills(skills: list[SkillLevel] | None) -> None:
    if skills is not None and len({s.skill for s in skills}) != len(skills):
        raise ValueError("Each skill can be listed only once")


class RuleCreate(BaseModel):
    name: RuleName = Field(description="Unique in the organization, ignoring case.")
    conditions: Conditions = Field(default_factory=Conditions)
    team_id: uuid.UUID = Field(description="An active team of the organization.")
    strategy: RoutingStrategy = RoutingStrategy.LEAST_LOADED
    required_skills: list[SkillLevel] = Field(
        default_factory=list,
        max_length=MAX_SKILLS,
        description="Agents need every one, at least at that level.",
    )
    is_active: bool = True

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        _distinct_skills(self.required_skills)
        return self


class RuleUpdate(BaseModel):
    """Only the fields provided are changed."""

    name: RuleName | None = None
    conditions: Conditions | None = None
    team_id: uuid.UUID | None = None
    strategy: RoutingStrategy | None = None
    required_skills: list[SkillLevel] | None = Field(default=None, max_length=MAX_SKILLS)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        _distinct_skills(self.required_skills)
        return self


class RuleOrder(BaseModel):
    rule_ids: list[uuid.UUID] = Field(
        description="Every rule of the organization, in the order to try them.", max_length=500
    )


class RuleTeam(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool


class RuleRead(BaseModel):
    id: uuid.UUID
    name: str
    position: int = Field(description="Rules are tried in this order (1 first).")
    conditions: Conditions
    team: RuleTeam
    strategy: RoutingStrategy
    required_skills: list[SkillLevel]
    is_active: bool
    version: int = Field(description="Goes up when what the rule does changes.")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def build(cls, rule: RoutingRule) -> Self:
        return cls(
            id=rule.id,
            name=rule.name,
            position=rule.position,
            conditions=Conditions.model_validate(rule.conditions),
            team=RuleTeam(id=rule.team.id, name=rule.team.name, is_active=rule.team.is_active),
            strategy=rule.strategy,
            required_skills=[SkillLevel.model_validate(s) for s in rule.required_skills],
            is_active=rule.is_active,
            version=rule.version,
            created_at=rule.created_at,
            updated_at=rule.updated_at,
        )


# --- Simulation and decisions -----------------------------------------------------------

CustomerValue = str | int | float | bool | None


class SimulationRequest(BaseModel):
    """Either an existing ticket, or a hypothetical one. Nothing is written."""

    ticket_id: uuid.UUID | None = Field(default=None, description="Simulate this ticket.")
    category: str | None = Field(default=None, max_length=50, description="A category key.")
    priority: TicketPriority | None = Field(default=None, description="Normal if omitted.")
    subject: str | None = Field(default=None, max_length=300)
    customer: dict[str, CustomerValue | list[CustomerValue]] = Field(
        default_factory=dict, description="The customer's metadata.", max_length=50
    )

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        hypothetical = {"category", "priority", "subject", "customer"} & self.model_fields_set
        if self.ticket_id is not None and hypothetical:
            raise ValueError("Give either ticket_id or a hypothetical ticket, not both")
        return self


class RuleCheckRead(BaseModel):
    rule_id: uuid.UUID | None
    name: str
    matched: bool
    detail: str = Field(description="What matched, or the first thing that didn't.")


class CandidateRead(BaseModel):
    user_id: uuid.UUID
    name: str
    eligible: bool
    reasons: list[str] = Field(description="Why they can't take it (empty when eligible).")
    active_tickets: int
    max_active_tickets: int
    skill_score: int
    rank: int | None = Field(description="1 = first choice; null when not eligible.")


class Named(BaseModel):
    id: uuid.UUID | None = Field(description="Null once it no longer exists.")
    name: str


class SimulationRead(BaseModel):
    outcome: RoutingOutcome
    rule: Named | None
    team: Named | None
    strategy: RoutingStrategy | None
    assignee: CandidateRead | None = Field(description="Who would be assigned.")
    rules: list[RuleCheckRead] = Field(description="Every rule tried, in order.")
    candidates: list[CandidateRead] = Field(description="Eligible by rank, then the others.")
    explanation: str


class DecisionRead(BaseModel):
    id: uuid.UUID
    trigger: RoutingTrigger
    applied: bool = Field(description="False in shadow mode: decided but not carried out.")
    actor: PersonSummary | None = Field(description="Who asked for a reroute.")
    outcome: RoutingOutcome
    rule: Named | None
    rule_version: int | None
    team: Named | None
    strategy: RoutingStrategy | None
    assignee: Named | None
    rules: list[RuleCheckRead]
    candidates: list[CandidateRead]
    explanation: str
    created_at: datetime

    @classmethod
    def build(cls, decision: RoutingDecision, actor: User | None) -> Self:
        def named(id_: uuid.UUID | None, name: str | None) -> Named | None:
            return Named(id=id_, name=name) if name is not None else None

        return cls(
            id=decision.id,
            trigger=decision.trigger,
            applied=decision.applied,
            actor=PersonSummary.build(actor),
            outcome=decision.outcome,
            rule=named(decision.rule_id, decision.rule_name),
            rule_version=decision.rule_version,
            team=named(decision.team_id, decision.team_name),
            strategy=decision.strategy,
            assignee=named(decision.assignee_id, decision.assignee_name),
            rules=[RuleCheckRead.model_validate(r) for r in decision.rules],
            candidates=[CandidateRead.model_validate(c) for c in decision.candidates],
            explanation=decision.explanation,
            created_at=decision.created_at,
        )
