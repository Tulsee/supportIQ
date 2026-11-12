"""Routing's pure logic (app/domain/routing.py): conditions, rule order, eligibility,
the three strategies, and the explanations people read. No database."""

import uuid
from datetime import UTC, datetime

from app.domain.routing import (
    AgentFacts,
    Condition,
    Operator,
    Outcome,
    Rule,
    Strategy,
    TicketFacts,
    assess,
    condition_matches,
    explain,
    match_rule,
    rank,
)

TICKET = TicketFacts(
    category="billing",
    priority="high",
    subject="Invoice is wrong",
    customer={"plan": "Gold", "seats": 25, "beta": True, "regions": ["EU", "US"], "note": ""},
)


def cond(field: str, operator: str, value: object) -> Condition:
    return Condition.from_json({"field": field, "operator": operator, "value": value})


def rule(name: str, *, all=(), any=(), strategy=Strategy.LEAST_LOADED, skills=()) -> Rule:
    return Rule(
        id=uuid.uuid4(),
        name=name,
        version=1,
        all=tuple(all),
        any=tuple(any),
        team_id=uuid.uuid4(),
        team_name="Billing",
        strategy=strategy,
        required_skills=tuple(skills),
    )


def agent(name: str, **overrides: object) -> AgentFacts:
    values: dict = {
        "user_id": uuid.UUID(int=sum(map(ord, name))),
        "name": name,
        "status": "available",
        "online": True,
        "active_tickets": 0,
        "capacity": 10,
        "skills": {},
        "last_assigned_at": None,
    }
    values.update(overrides)
    return AgentFacts(**values)


def at(hour: int) -> datetime:
    return datetime(2026, 11, 16, hour, tzinfo=UTC)


# --- Conditions -----------------------------------------------------------------------------


def test_conditions_compare_ignoring_case() -> None:
    assert condition_matches(cond("category", "eq", "Billing"), TICKET)
    assert condition_matches(cond("priority", "in", ["urgent", "high"]), TICKET)
    assert not condition_matches(cond("priority", "in", ["low", "normal"]), TICKET)
    assert condition_matches(cond("subject", "contains", "INVOICE"), TICKET)
    assert not condition_matches(cond("subject", "contains", "refund"), TICKET)


def test_customer_metadata_of_any_simple_type() -> None:
    assert condition_matches(cond("customer.plan", "eq", "gold"), TICKET)
    assert condition_matches(cond("customer.seats", "eq", "25"), TICKET)
    assert condition_matches(cond("customer.beta", "eq", "true"), TICKET)
    assert condition_matches(cond("customer.regions", "in", ["us"]), TICKET)  # any element
    assert condition_matches(cond("customer.plan", "is_set", True), TICKET)
    assert condition_matches(cond("customer.note", "is_set", False), TICKET)  # blank = not set
    assert condition_matches(cond("customer.missing", "is_set", False), TICKET)
    assert not condition_matches(cond("customer.missing", "eq", "x"), TICKET)


def test_uncategorized_tickets() -> None:
    uncategorized = TicketFacts(category=None, priority="normal", subject="Hi")
    assert condition_matches(cond("category", "is_set", False), uncategorized)
    assert not condition_matches(cond("category", "in", ["billing"]), uncategorized)


# --- Rules ----------------------------------------------------------------------------------


def test_all_conditions_and_one_of_any() -> None:
    both = rule(
        "VIP billing",
        all=[cond("category", "eq", "billing")],
        any=[cond("customer.plan", "eq", "platinum"), cond("priority", "eq", "high")],
    )
    matched, checks = match_rule([both], TICKET)
    assert matched is both
    assert checks[0].detail == "category is billing, priority is high"

    neither = rule(
        "Neither", all=[cond("category", "eq", "billing")], any=[cond("priority", "eq", "low")]
    )
    assert match_rule([neither], TICKET)[1][0].detail == "not priority is low"
    wrong = rule("Wrong", all=[cond("subject", "contains", "refund")])
    assert match_rule([wrong], TICKET)[1][0].detail == "not subject contains “refund”"


def test_the_first_matching_rule_wins_and_no_conditions_match_everything() -> None:
    returns = rule("Returns", all=[cond("category", "eq", "returns")])
    catch_all = rule("Everything else")
    later = rule("Billing", all=[cond("category", "eq", "billing")])
    matched, checks = match_rule([returns, catch_all, later], TICKET)
    assert matched is catch_all
    assert [(c.name, c.matched) for c in checks] == [("Returns", False), ("Everything else", True)]
    assert checks[1].detail == "matches every ticket"
    assert match_rule([], TICKET) == (None, [])


# --- Eligibility and strategies -------------------------------------------------------------


def test_why_agents_are_not_eligible() -> None:
    required = [("refunds", 3), ("billing", 2)]
    assert assess(agent("Off", online=False, status="busy"), []).reasons == ("offline",)
    assert assess(agent("Busy", status="busy"), []).reasons == ("busy",)
    assert assess(agent("Away", status="away"), []).reasons == ("away",)
    assert assess(agent("Full", active_tickets=5, capacity=5), []).reasons == (
        "at capacity, 5 of 5",
    )
    assert assess(agent("Zero", capacity=0), []).reasons == ("takes no automatic assignments",)
    weak = assess(agent("Weak", skills={"refunds": 1}), required)
    assert weak.reasons == ("refunds level 1, needs 3", "no billing skill, needs 2")
    strong = assess(agent("Strong", skills={"refunds": 5, "billing": 2}), required)
    assert strong.eligible and strong.skill_score == 7


def test_least_loaded_uses_share_of_capacity_then_longest_wait() -> None:
    agents = [
        agent("Half", active_tickets=5, capacity=10),
        agent("Quarter", active_tickets=1, capacity=4, last_assigned_at=at(9)),
        agent("Quarter, waited", active_tickets=2, capacity=8, last_assigned_at=at(8)),
        agent("Away", status="away"),
    ]
    ranked = rank(agents, Strategy.LEAST_LOADED, [])
    assert [(c.name, c.rank) for c in ranked] == [
        ("Quarter, waited", 1),
        ("Quarter", 2),
        ("Half", 3),
        ("Away", None),
    ]


def test_round_robin_takes_whoever_waited_longest_and_newcomers_first() -> None:
    agents = [
        agent("Recent", last_assigned_at=at(11)),
        agent("Earlier", last_assigned_at=at(9), active_tickets=8),
        agent("Never"),
    ]
    assert [c.name for c in rank(agents, Strategy.ROUND_ROBIN, [])] == [
        "Never",
        "Earlier",
        "Recent",
    ]


def test_skill_match_prefers_the_strongest_then_the_least_loaded() -> None:
    required = [("refunds", 2)]
    agents = [
        agent("Good", skills={"refunds": 3}),
        agent("Expert, busy", skills={"refunds": 5}, active_tickets=6),
        agent("Expert", skills={"refunds": 5}, active_tickets=2),
        agent("Novice", skills={"refunds": 1}),
    ]
    ranked = rank(agents, Strategy.SKILL_MATCH, required)
    assert [(c.name, c.rank) for c in ranked] == [
        ("Expert", 1),
        ("Expert, busy", 2),
        ("Good", 3),
        ("Novice", None),
    ]


# --- Explanations ---------------------------------------------------------------------------


def test_explanations() -> None:
    billing = rule("Billing", all=[cond("category", "eq", "billing")])
    matched, checks = match_rule([billing], TICKET)
    candidates = rank(
        [agent("Abe", active_tickets=2), agent("Lena", status="busy")], Strategy.LEAST_LOADED, []
    )
    assigned = explain(
        rule=matched,
        checks=checks,
        candidates=candidates,
        outcome=Outcome.ASSIGNED,
        assignee=candidates[0],
    )
    assert assigned == (
        "Rule “Billing” matched (category is billing) and sent the ticket to the Billing team. "
        "Assigned to Abe, the least loaded available agent (2 of 10 active tickets). "
        "Not eligible: Lena (busy)."
    )
    shadow = explain(
        rule=matched,
        checks=checks,
        candidates=candidates,
        outcome=Outcome.ASSIGNED,
        assignee=candidates[0],
        applied=False,
    )
    assert "Would assign to Abe" in shadow

    nobody = rank(
        [agent("Lena", online=False), agent("Sam", active_tickets=3, capacity=3)],
        Strategy.LEAST_LOADED,
        [],
    )
    no_agent = explain(
        rule=matched, checks=checks, candidates=nobody, outcome=Outcome.NO_AGENT, assignee=None
    )
    assert no_agent.endswith(
        "Nobody there can take it now: Lena (offline), Sam (at capacity, 3 of 3). "
        "It stays unassigned."
    )
    empty = explain(
        rule=matched, checks=checks, candidates=[], outcome=Outcome.NO_AGENT, assignee=None
    )
    assert empty.endswith("The team has no members, so the ticket stays unassigned.")

    manual = rule("VIP", strategy=Strategy.MANUAL)
    m, mc = match_rule([manual], TICKET)
    assert explain(
        rule=m, checks=mc, candidates=[], outcome=Outcome.TEAM_ONLY, assignee=None
    ).endswith("Its strategy is manual, so someone in the team picks it up.")

    returns = rule("Returns", all=[cond("category", "eq", "returns")])
    _, one = match_rule([returns], TICKET)
    assert explain(
        rule=None, checks=one, candidates=[], outcome=Outcome.NO_RULE, assignee=None
    ) == (
        "The only active routing rule, “Returns”, didn't match (not category is returns), "
        "so the ticket stays in the unassigned queue."
    )
    _, two = match_rule([returns, returns], TICKET)
    assert explain(
        rule=None, checks=two, candidates=[], outcome=Outcome.NO_RULE, assignee=None
    ).startswith("None of the 2 active routing rules matched")
    assert "no active routing rules" in explain(
        rule=None, checks=[], candidates=[], outcome=Outcome.NO_RULE, assignee=None
    )


def test_operator_names_are_stable() -> None:
    # Stored in the database as JSON: renaming one would break saved rules.
    assert [o.value for o in Operator] == ["eq", "in", "contains", "is_set"]
