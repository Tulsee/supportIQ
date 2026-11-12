"""Routing as pure functions (Module 10, phase 6): which rule a ticket matches, which
agents of the rule's team can take it, in what order, and why, in words.

No database, Redis or FastAPI: ``app/services/routing.py`` gathers the facts, calls
these, and applies the result (with locks, so concurrent runs stay correct).

Conditions are a constrained format, never expressions::

    {"all": [{"field": "category", "operator": "in", "value": ["billing", "refunds"]}],
     "any": [{"field": "subject", "operator": "contains", "value": "invoice"}]}

Every ``all`` condition must hold, and at least one ``any`` condition if there are
any. No conditions at all matches every ticket (a catch-all rule). Comparisons
ignore case.
"""

import enum
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

CUSTOMER_PREFIX = "customer."


class Operator(enum.StrEnum):
    EQ = "eq"
    IN = "in"
    CONTAINS = "contains"
    IS_SET = "is_set"


# Which operators each field takes ("customer.<key>" fields: all of them).
FIELD_OPERATORS: dict[str, frozenset[Operator]] = {
    "category": frozenset({Operator.EQ, Operator.IN, Operator.IS_SET}),
    "priority": frozenset({Operator.EQ, Operator.IN}),
    "subject": frozenset({Operator.CONTAINS}),
    "language": frozenset({Operator.EQ, Operator.IN, Operator.IS_SET}),  # from triage
}
CUSTOMER_OPERATORS = frozenset(Operator)


class Strategy(enum.StrEnum):
    LEAST_LOADED = "least_loaded"
    ROUND_ROBIN = "round_robin"
    SKILL_MATCH = "skill_match"
    MANUAL = "manual"


class Outcome(enum.StrEnum):
    ASSIGNED = "assigned"
    TEAM_ONLY = "team_only"
    NO_AGENT = "no_agent"
    NO_RULE = "no_rule"


# --- Conditions and rules ---------------------------------------------------------------


@dataclass(frozen=True)
class Condition:
    field: str
    operator: Operator
    value: str | tuple[str, ...] | bool

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Condition":
        value = raw.get("value")
        if isinstance(value, list):
            value = tuple(str(v) for v in value)
        return cls(str(raw["field"]), Operator(raw["operator"]), value)

    def describe(self, *, negated: bool = False) -> str:
        """In words: "category is billing", or with ``negated``, "category isn't billing"."""
        name = self.field.replace(CUSTOMER_PREFIX, "customer ").replace("_", " ")
        values = list(self.value) if isinstance(self.value, tuple) else [str(self.value)]
        match self.operator:
            case Operator.EQ | Operator.IN if len(values) == 1:
                verb = "isn't" if negated else "is"
                return f"{name} {verb} {values[0]}"
            case Operator.IN:
                listed = ", ".join(values[:-1]) + f" or {values[-1]}"
                return (
                    f"{name} is none of {', '.join(values)}" if negated else f"{name} is {listed}"
                )
            case Operator.CONTAINS:
                verb = "doesn't contain" if negated else "contains"
                return f"{name} {verb} “{self.value}”"
            case _:  # is_set
                is_set = bool(self.value) != negated
                return f"{name} is set" if is_set else f"{name} is not set"


@dataclass(frozen=True)
class TicketFacts:
    """What rules may look at. Never message content."""

    category: str | None  # the category's key
    priority: str
    subject: str
    customer: Mapping[str, Any] = field(default_factory=dict)  # the customer's metadata
    language: str | None = None  # ISO 639-1, detected by triage

    def values(self, name: str) -> list[str]:
        """The field's value(s) as text; empty when not set."""
        if name.startswith(CUSTOMER_PREFIX):
            raw = self.customer.get(name.removeprefix(CUSTOMER_PREFIX))
        else:
            raw = {
                "category": self.category,
                "priority": self.priority,
                "subject": self.subject,
                "language": self.language,
            }.get(name)
        items = raw if isinstance(raw, list | tuple) else [raw]
        texts = []
        for item in items:
            if item is None or isinstance(item, dict | list):
                continue
            text = ("true" if item else "false") if isinstance(item, bool) else str(item)
            if text.strip():
                texts.append(text)
        return texts


def condition_matches(condition: Condition, facts: TicketFacts) -> bool:
    values = [v.casefold() for v in facts.values(condition.field)]
    if condition.operator == Operator.IS_SET:
        return bool(values) == bool(condition.value)
    if not values:
        return False
    match condition.operator:
        case Operator.EQ:
            return str(condition.value).casefold() in values
        case Operator.IN:
            wanted = {str(v).casefold() for v in condition.value}  # type: ignore[union-attr]
            return any(v in wanted for v in values)
        case Operator.CONTAINS:
            needle = str(condition.value).casefold()
            return any(needle in v for v in values)
    return False


@dataclass(frozen=True)
class Rule:
    id: uuid.UUID
    name: str
    version: int
    all: tuple[Condition, ...]
    any: tuple[Condition, ...]
    team_id: uuid.UUID
    team_name: str
    strategy: Strategy
    required_skills: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class RuleCheck:
    rule_id: uuid.UUID
    name: str
    matched: bool
    detail: str  # what matched, or the first thing that didn't

    def to_json(self) -> dict[str, Any]:
        return {
            "rule_id": str(self.rule_id),
            "name": self.name,
            "matched": self.matched,
            "detail": self.detail,
        }


def check_rule(rule: Rule, facts: TicketFacts) -> RuleCheck:
    for condition in rule.all:
        if not condition_matches(condition, facts):
            return RuleCheck(rule.id, rule.name, False, condition.describe(negated=True))
    reasons = [c.describe() for c in rule.all]
    if rule.any:
        hit = next((c for c in rule.any if condition_matches(c, facts)), None)
        if hit is None:
            if len(rule.any) == 1:
                detail = rule.any[0].describe(negated=True)
            else:
                detail = "none of: " + "; ".join(c.describe() for c in rule.any)
            return RuleCheck(rule.id, rule.name, False, detail)
        reasons.append(hit.describe())
    return RuleCheck(rule.id, rule.name, True, ", ".join(reasons) or "matches every ticket")


def match_rule(rules: Sequence[Rule], facts: TicketFacts) -> tuple[Rule | None, list[RuleCheck]]:
    """The first rule (in order) that matches, and every check made on the way."""
    checks = []
    for rule in rules:
        check = check_rule(rule, facts)
        checks.append(check)
        if check.matched:
            return rule, checks
    return None, checks


# --- Agents -------------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentFacts:
    user_id: uuid.UUID
    name: str
    status: str  # available, busy, away
    online: bool
    active_tickets: int
    capacity: int
    skills: Mapping[str, int]
    last_assigned_at: datetime | None


@dataclass(frozen=True)
class Candidate:
    user_id: uuid.UUID
    name: str
    reasons: tuple[str, ...]  # why they can't take it; empty = eligible
    active_tickets: int
    capacity: int
    skill_score: int  # total level in the rule's required skills
    last_assigned_at: datetime | None
    rank: int | None = None  # 1 = first choice; None = not eligible

    @property
    def eligible(self) -> bool:
        return not self.reasons

    @property
    def load(self) -> float:
        return self.active_tickets / self.capacity if self.capacity else 1.0

    def to_json(self) -> dict[str, Any]:
        return {
            "user_id": str(self.user_id),
            "name": self.name,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "active_tickets": self.active_tickets,
            "max_active_tickets": self.capacity,
            "skill_score": self.skill_score,
            "rank": self.rank,
        }

    def summary(self) -> str:
        return f"{self.name} ({'; '.join(self.reasons)})"


def assess(agent: AgentFacts, required_skills: Sequence[tuple[str, int]]) -> Candidate:
    reasons = []
    if not agent.online:
        reasons.append("offline")
    elif agent.status != "available":
        reasons.append(agent.status)
    if agent.capacity <= 0:
        reasons.append("takes no automatic assignments")
    elif agent.active_tickets >= agent.capacity:
        reasons.append(f"at capacity, {agent.active_tickets} of {agent.capacity}")
    for skill, level in required_skills:
        has = agent.skills.get(skill, 0)
        if has < level:
            reasons.append(
                f"{skill} level {has}, needs {level}" if has else f"no {skill} skill, needs {level}"
            )
    return Candidate(
        user_id=agent.user_id,
        name=agent.name,
        reasons=tuple(reasons),
        active_tickets=agent.active_tickets,
        capacity=agent.capacity,
        skill_score=sum(agent.skills.get(skill, 0) for skill, _ in required_skills),
        last_assigned_at=agent.last_assigned_at,
    )


def _waited(candidate: Candidate) -> tuple[int, datetime | None]:
    # Never assigned automatically comes first, then the longest wait.
    at = candidate.last_assigned_at
    return (0, None) if at is None else (1, at)


def _order_key(strategy: Strategy, c: Candidate) -> tuple[Any, ...]:
    if strategy == Strategy.ROUND_ROBIN:
        return (_waited(c), str(c.user_id))
    if strategy == Strategy.SKILL_MATCH:
        return (-c.skill_score, c.load, _waited(c), str(c.user_id))
    return (c.load, _waited(c), str(c.user_id))  # least loaded


def rank(
    agents: Sequence[AgentFacts], strategy: Strategy, required_skills: Sequence[tuple[str, int]]
) -> list[Candidate]:
    """Every agent assessed: the eligible ones in the strategy's order (rank 1 first),
    then the others by name."""
    assessed = [assess(agent, required_skills) for agent in agents]
    eligible = sorted((c for c in assessed if c.eligible), key=lambda c: _order_key(strategy, c))
    others = sorted((c for c in assessed if not c.eligible), key=lambda c: c.name.casefold())
    ranked = [
        Candidate(**{**c.__dict__, "rank": position}) for position, c in enumerate(eligible, 1)
    ]
    return ranked + others


# --- Explanations --------------------------------------------------------------------------

_WHY = {
    Strategy.LEAST_LOADED: "the least loaded available agent",
    Strategy.ROUND_ROBIN: "next in turn (assigned automatically least recently)",
    Strategy.SKILL_MATCH: "the best skill match",
}


def explain(
    *,
    rule: Rule | None,
    checks: Sequence[RuleCheck],
    candidates: Sequence[Candidate],
    outcome: Outcome,
    assignee: Candidate | None,
    applied: bool = True,
) -> str:
    """One paragraph a person can read on the ticket."""
    if rule is None:
        if not checks:
            return "There are no active routing rules, so the ticket stays in the unassigned queue."
        if len(checks) == 1:
            return (
                f"The only active routing rule, “{checks[0].name}”, didn't match "
                f"({checks[0].detail}), so the ticket stays in the unassigned queue."
            )
        return (
            f"None of the {len(checks)} active routing rules matched, so the ticket stays in "
            "the unassigned queue."
        )
    check = checks[-1]
    sentences = [
        f"Rule “{rule.name}” matched ({check.detail}) "
        f"and sent the ticket to the {rule.team_name} team."
    ]
    if outcome == Outcome.TEAM_ONLY:
        sentences.append("Its strategy is manual, so someone in the team picks it up.")
    elif outcome == Outcome.ASSIGNED and assignee is not None:
        why = _WHY.get(rule.strategy, "the first choice")
        detail = f"{assignee.active_tickets} of {assignee.capacity} active tickets"
        if rule.strategy == Strategy.SKILL_MATCH and rule.required_skills:
            detail = f"skill total {assignee.skill_score}, {detail}"
        verb = "Assigned" if applied else "Would assign"
        sentences.append(f"{verb} to {assignee.name}, {why} ({detail}).")
        passed = [c for c in candidates if not c.eligible]
        if passed:
            sentences.append("Not eligible: " + ", ".join(c.summary() for c in passed) + ".")
    else:
        if not candidates:
            sentences.append("The team has no members, so the ticket stays unassigned.")
        else:
            sentences.append(
                "Nobody there can take it now: "
                + ", ".join(c.summary() for c in candidates)
                + ". It stays unassigned."
            )
    return " ".join(sentences)
