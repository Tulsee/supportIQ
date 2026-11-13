"""Analytics (Module 7): support operations and AI performance for one organization.

Everything is computed in PostgreSQL over a trailing window of days, scoped to the
organization (and by row-level security). Durations are medians, so a few very old
tickets don't dominate. Days are UTC calendar days.
"""

import json
import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.business_time import business_minutes_between, calendar_from_snapshot
from app.models import DEFAULT_MAX_ACTIVE_TICKETS, OrganizationMember
from app.schemas.organizations import OrganizationSettings
from app.services.authorization import STAFF_ROLES, require_role

_TICKETS = text(
    """
    SELECT
      count(*) FILTER (WHERE status NOT IN ('resolved', 'closed')) AS open,
      count(*) FILTER (WHERE status NOT IN ('resolved', 'closed') AND assignee_id IS NULL)
        AS unassigned,
      count(*) FILTER (WHERE status NOT IN ('resolved', 'closed') AND priority = 'urgent')
        AS urgent,
      count(*) FILTER (WHERE created_at >= :since) AS created,
      count(*) FILTER (WHERE resolved_at >= :since) AS resolved
    FROM tickets WHERE organization_id = :org
    """
)
_BY = text(
    """
    SELECT status AS key, count(*) AS n FROM tickets
    WHERE organization_id = :org AND status NOT IN ('resolved', 'closed') GROUP BY status
    """
)
_DAILY_CREATED = text(
    """
    SELECT (created_at AT TIME ZONE 'UTC')::date AS day, count(*) AS n FROM tickets
    WHERE organization_id = :org AND created_at >= :since GROUP BY 1
    """
)
_DAILY_RESOLVED = text(
    """
    SELECT (resolved_at AT TIME ZONE 'UTC')::date AS day, count(*) AS n FROM tickets
    WHERE organization_id = :org AND resolved_at >= :since GROUP BY 1
    """
)
_RESPONSE_TIMES = text(
    """
    SELECT
      percentile_cont(0.5) WITHIN GROUP (ORDER BY first_reply - created_at) AS first_response,
      count(first_reply) AS replied
    FROM (
      SELECT t.created_at,
        -- The first public staff message after the opening one: when staff log a
        -- ticket for a customer, the opening message is theirs but isn't a reply.
        (SELECT min(m.created_at) FROM ticket_messages m
          WHERE m.ticket_id = t.id AND m.author_type = 'agent' AND NOT m.is_internal
            AND m.seq > (SELECT min(o.seq) FROM ticket_messages o WHERE o.ticket_id = t.id))
          AS first_reply
      FROM tickets t WHERE t.organization_id = :org AND t.created_at >= :since
    ) x
    """
)
_RESOLUTION = text(
    """
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY resolved_at - created_at) AS resolution
    FROM tickets WHERE organization_id = :org AND resolved_at >= :since
    """
)
_COUNTS = text(
    """
    SELECT
      (SELECT count(*) FROM customers WHERE organization_id = :org) AS customers,
      (SELECT count(*) FROM documents WHERE organization_id = :org AND status = 'indexed')
        AS documents
    """
)

# SLA compliance for clocks started in the period (first cycles; reopened cycles are
# counted separately). The rate counts only clocks with an outcome: met on time, met
# late, or past the deadline unmet. Clocks still within their deadline, and cancelled
# ones (closed unresolved, or no policy after a priority change), are reported apart.
_SLA = text(
    """
    WITH c AS (
      SELECT kind, cycle, state, met_at, due_at,
        (breached_at IS NOT NULL OR (met_at IS NOT NULL AND met_at > due_at)
         OR (met_at IS NULL AND state = 'running' AND due_at <= now())) AS past_due
      FROM ticket_sla_clocks
      WHERE organization_id = :org AND started_at >= :since
    )
    SELECT kind,
      count(*) FILTER (WHERE cycle = 1 AND met_at IS NOT NULL AND NOT past_due) AS on_time,
      count(*) FILTER (WHERE cycle = 1 AND met_at IS NOT NULL AND past_due) AS late,
      count(*) FILTER (WHERE cycle = 1 AND met_at IS NULL AND state <> 'cancelled' AND past_due)
        AS overdue,
      count(*) FILTER (WHERE cycle = 1 AND met_at IS NULL AND state <> 'cancelled'
        AND NOT past_due) AS open,
      count(*) FILTER (WHERE cycle = 1 AND state = 'cancelled') AS cancelled,
      count(*) FILTER (WHERE cycle > 1) AS reopened
    FROM c GROUP BY kind
    """
)
_SLA_CONFIGURED = text(
    "SELECT EXISTS (SELECT 1 FROM business_calendars WHERE organization_id = :org) AS configured"
)

_RUNS_DAILY = text(
    """
    SELECT (created_at AT TIME ZONE 'UTC')::date AS day, kind, count(*) AS n FROM ai_runs
    WHERE organization_id = :org AND created_at >= :since GROUP BY 1, 2
    """
)
_STATUSES = text(
    """
    SELECT r.kind, a.status, count(*) AS n
    FROM ai_runs r JOIN ai_answers a ON a.ai_run_id = r.id
    WHERE r.organization_id = :org AND r.created_at >= :since
      AND r.kind IN ('answer', 'suggestion')
    GROUP BY r.kind, a.status
    """
)
_LATENCY = text(
    """
    SELECT kind,
      percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
      percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
      count(*) AS n
    FROM ai_runs WHERE organization_id = :org AND created_at >= :since
    GROUP BY kind
    """
)
_REASONS = text(
    """
    SELECT e.decision, e.reason, count(*) AS n, avg(e.score) AS score
    FROM evidence_assessments e JOIN ai_runs r ON r.id = e.ai_run_id
    WHERE r.organization_id = :org AND r.created_at >= :since
    GROUP BY e.decision, e.reason ORDER BY n DESC
    """
)
_FEEDBACK = text(
    """
    SELECT f.rating, count(*) AS n
    FROM ai_feedback f JOIN ai_runs r ON r.id = f.ai_run_id
    WHERE r.organization_id = :org AND f.created_at >= :since
    GROUP BY f.rating
    """
)
_TOKENS = text(
    """
    SELECT a.provider, a.model, count(*) AS n,
      coalesce(sum(a.input_tokens), 0) AS input_tokens,
      coalesce(sum(a.output_tokens), 0) AS output_tokens
    FROM ai_answers a JOIN ai_runs r ON r.id = a.ai_run_id
    WHERE r.organization_id = :org AND r.created_at >= :since AND a.status <> 'no_passages'
    GROUP BY a.provider, a.model ORDER BY n DESC
    """
)
_GAPS = text(
    """
    SELECT lower(r.query) AS query, count(*) AS n, max(r.created_at) AS last_asked,
      (array_agg(r.id ORDER BY r.created_at DESC))[1] AS run_id,
      (array_agg(a.status ORDER BY r.created_at DESC))[1] AS status
    FROM ai_runs r JOIN ai_answers a ON a.ai_run_id = r.id
    WHERE r.organization_id = :org AND r.created_at >= :since
      AND r.kind IN ('answer', 'suggestion')
      AND a.status IN ('abstained', 'declined', 'no_passages', 'escalated', 'clarify')
    GROUP BY lower(r.query) ORDER BY n DESC, last_asked DESC LIMIT 10
    """
)

# --- Routing (Module 10) ----------------------------------------------------------------
# An assignment is automatic when routing made it (its event says source "routing"),
# whether on its own or because someone asked for a reroute.
_ROUTING_DECISIONS = text(
    """
    SELECT applied, outcome, count(*) AS n FROM routing_decisions
    WHERE organization_id = :org AND created_at >= :since GROUP BY applied, outcome
    """
)
# The first assignment of each ticket opened in the period: by routing or by a person,
# and how long after the ticket was opened.
_FIRST_ASSIGNMENTS = text(
    """
    WITH first AS (
      SELECT DISTINCT ON (e.ticket_id) e.ticket_id, e.created_at,
        coalesce(e.data->>'source', '') = 'routing' AS automatic
      FROM ticket_events e JOIN tickets t ON t.id = e.ticket_id
      WHERE t.organization_id = :org AND t.created_at >= :since AND e.type = 'assigned'
      ORDER BY e.ticket_id, e.seq
    )
    SELECT count(*) AS assigned,
      count(*) FILTER (WHERE f.automatic) AS automatic,
      percentile_cont(0.5) WITHIN GROUP (ORDER BY f.created_at - t.created_at) AS wait,
      percentile_cont(0.5) WITHIN GROUP (ORDER BY f.created_at - t.created_at)
        FILTER (WHERE f.automatic) AS wait_automatic,
      percentile_cont(0.5) WITHIN GROUP (ORDER BY f.created_at - t.created_at)
        FILTER (WHERE NOT f.automatic) AS wait_manual
    FROM first f JOIN tickets t ON t.id = f.ticket_id
    """
)
# Automatic assignments in the period, and how many a person changed (reassigned or
# unassigned) within an hour.
_OVERRIDES = text(
    """
    SELECT count(*) AS automatic,
      count(*) FILTER (WHERE EXISTS (
        SELECT 1 FROM ticket_events n
        WHERE n.ticket_id = a.ticket_id AND n.seq > a.seq
          AND n.type IN ('assigned', 'unassigned')
          AND coalesce(n.data->>'source', '') <> 'routing'
          AND n.created_at <= a.created_at + interval '1 hour'
      )) AS changed
    FROM ticket_events a
    WHERE a.organization_id = :org AND a.created_at >= :since AND a.type = 'assigned'
      AND a.data->>'source' = 'routing'
    """
)
# Shadow decisions that picked someone, against who a person then assigned first.
_SHADOW = text(
    """
    SELECT count(*) AS decided,
      count(f.assignee) AS compared,
      count(*) FILTER (WHERE f.assignee = d.assignee_id::text) AS agreed
    FROM routing_decisions d
    LEFT JOIN LATERAL (
      SELECT e.data->'to'->>'id' AS assignee FROM ticket_events e
      WHERE e.ticket_id = d.ticket_id AND e.type = 'assigned'
        AND coalesce(e.data->>'source', '') <> 'routing'
      ORDER BY e.seq LIMIT 1
    ) f ON true
    WHERE d.organization_id = :org AND d.created_at >= :since
      AND NOT d.applied AND d.outcome = 'assigned'
    """
)
# Every admin and agent: tickets they work on now (assigned or in progress) against
# their capacity, and how they got assignments in the period.
_WORKLOAD = text(
    """
    SELECT u.id AS user_id, u.full_name AS name,
      coalesce(p.max_active_tickets, :default_capacity) AS capacity,
      (SELECT count(*) FROM tickets t
        WHERE t.organization_id = :org AND t.assignee_id = u.id
          AND t.status IN ('assigned', 'in_progress')) AS active,
      (SELECT count(*) FROM ticket_events e
        WHERE e.organization_id = :org AND e.created_at >= :since AND e.type = 'assigned'
          AND e.data->'to'->>'id' = u.id::text AND e.data->>'source' = 'routing')
        AS automatic,
      (SELECT count(*) FROM ticket_events e
        WHERE e.organization_id = :org AND e.created_at >= :since AND e.type = 'assigned'
          AND e.data->'to'->>'id' = u.id::text
          AND coalesce(e.data->>'source', '') <> 'routing') AS manual
    FROM organization_members m
    JOIN users u ON u.id = m.user_id
    LEFT JOIN agent_profiles p ON p.organization_id = m.organization_id AND p.user_id = u.id
    WHERE m.organization_id = :org AND m.role IN ('admin', 'agent')
    ORDER BY lower(u.full_name), u.id
    """
)

# --- SLA detail (Module 10) ---------------------------------------------------------------
# First cycles started in the period, grouped by the ticket's priority, team or assignee
# as they are now. The same outcome rules as _SLA.
_SLA_BY = """
    WITH c AS (
      SELECT k.kind, k.state, k.met_at, t.{column} AS key,
        (k.breached_at IS NOT NULL OR (k.met_at IS NOT NULL AND k.met_at > k.due_at)
         OR (k.met_at IS NULL AND k.state = 'running' AND k.due_at <= now())) AS past_due
      FROM ticket_sla_clocks k JOIN tickets t ON t.id = k.ticket_id
      WHERE k.organization_id = :org AND k.started_at >= :since AND k.cycle = 1
    )
    SELECT key, kind,
      count(*) FILTER (WHERE met_at IS NOT NULL AND NOT past_due) AS on_time,
      count(*) FILTER (WHERE met_at IS NOT NULL AND past_due) AS late,
      count(*) FILTER (WHERE met_at IS NULL AND state <> 'cancelled' AND past_due) AS overdue,
      count(*) FILTER (WHERE met_at IS NULL AND state <> 'cancelled' AND NOT past_due) AS open,
      count(*) FILTER (WHERE state = 'cancelled') AS cancelled
    FROM c GROUP BY key, kind
"""
_SLA_GROUPS = {
    "priority": text(_SLA_BY.format(column="priority")),
    "team": text(_SLA_BY.format(column="team_id")),
    "agent": text(_SLA_BY.format(column="assignee_id")),
}
_TEAM_NAMES = text("SELECT id, name FROM teams WHERE organization_id = :org")
_STAFF_NAMES = text(
    """
    SELECT u.id, u.full_name AS name FROM users u
    JOIN organization_members m ON m.user_id = u.id WHERE m.organization_id = :org
    """
)
# Met first cycles started in the period (newest first, bounded), for business-time
# medians computed with each clock's own calendar.
_MET_CLOCKS = text(
    """
    SELECT kind, started_at, met_at, paused_minutes, calendar_snapshot
    FROM ticket_sla_clocks
    WHERE organization_id = :org AND started_at >= :since AND cycle = 1
      AND met_at IS NOT NULL
    ORDER BY started_at DESC LIMIT 5000
    """
)
_SLA_DAILY = text(
    """
    SELECT 'at_risk' AS kind, (at_risk_at AT TIME ZONE 'UTC')::date AS day, count(*) AS n
    FROM ticket_sla_clocks WHERE organization_id = :org AND at_risk_at >= :since GROUP BY 2
    UNION ALL
    SELECT 'breached', (breached_at AT TIME ZONE 'UTC')::date, count(*)
    FROM ticket_sla_clocks WHERE organization_id = :org AND breached_at >= :since GROUP BY 2
    """
)
_PAUSES = text(
    """
    SELECT reason, count(*) AS n, count(*) FILTER (WHERE ended_at IS NULL) AS ongoing,
      percentile_cont(0.5) WITHIN GROUP (ORDER BY ended_at - started_at)
        FILTER (WHERE ended_at IS NOT NULL) AS median
    FROM ticket_sla_pauses
    WHERE organization_id = :org AND started_at >= :since
    GROUP BY reason ORDER BY n DESC
    """
)

# --- Triage (Module 11) -------------------------------------------------------------------
# Predictions made in the period, with what people did with them. A prediction is
# "correct" when the person kept or accepted its category (confirmed).
_TRIAGE = text(
    """
    SELECT decision, outcome, count(*) AS n,
      count(*) FILTER (WHERE applied) AS applied
    FROM ticket_triage WHERE organization_id = :org AND created_at >= :since
    GROUP BY decision, outcome
    """
)
_TRIAGE_CATEGORIES = text(
    """
    SELECT p.category_key AS key, coalesce(c.name, p.category_name, p.category_key) AS name,
      count(*) AS predicted,
      count(*) FILTER (WHERE p.outcome IS NOT NULL) AS reviewed,
      count(*) FILTER (WHERE p.outcome = 'confirmed') AS correct
    FROM ticket_triage p
    LEFT JOIN ticket_categories c
      ON c.organization_id = p.organization_id AND c.key = p.category_key
    WHERE p.organization_id = :org AND p.created_at >= :since AND p.decision = 'confident'
    GROUP BY 1, 2 ORDER BY predicted DESC, 1
    """
)
_TRIAGE_LATENCY = text(
    """
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50
    FROM ticket_triage WHERE organization_id = :org AND created_at >= :since
    """
)

_SELF_SERVICE = text(
    """
    SELECT count(*) AS conversations,
      count(*) FILTER (WHERE status = 'resolved') AS resolved,
      count(*) FILTER (WHERE status = 'handed_off') AS handed_off,
      count(*) FILTER (WHERE status = 'open') AS open
    FROM assistant_conversations WHERE organization_id = :org AND created_at >= :since
    """
)
_SELF_SERVICE_REPLIES = text(
    """
    SELECT m.outcome,
      CASE WHEN m.ai_run_id IS NULL THEN 'unavailable' ELSE e.reason END AS reason,
      count(*) AS n,
      count(*) FILTER (WHERE m.rating IS NOT NULL) AS rated,
      count(*) FILTER (WHERE m.rating = 'helpful') AS helpful
    FROM assistant_messages m
    LEFT JOIN evidence_assessments e ON e.ai_run_id = m.ai_run_id
    WHERE m.organization_id = :org AND m.created_at >= :since AND m.role = 'assistant'
    GROUP BY 1, 2
    """
)
_SELF_SERVICE_UNANSWERED = text(
    """
    SELECT q.body AS question, q.created_at AS asked_at,
      CASE WHEN r.ai_run_id IS NULL THEN 'unavailable' ELSE e.reason END AS reason,
      r.conversation_id, c.ticket_id, r.ai_run_id
    FROM assistant_messages r
    JOIN assistant_conversations c ON c.id = r.conversation_id
    JOIN LATERAL (
      SELECT body, created_at FROM assistant_messages q
      WHERE q.conversation_id = r.conversation_id AND q.role = 'customer' AND q.seq < r.seq
      ORDER BY q.seq DESC LIMIT 1
    ) q ON true
    LEFT JOIN evidence_assessments e ON e.ai_run_id = r.ai_run_id
    WHERE r.organization_id = :org AND r.created_at >= :since
      AND r.role = 'assistant' AND r.outcome = 'unable'
    ORDER BY r.seq DESC LIMIT 10
    """
)
_SELF_SERVICE_DAILY = text(
    """
    SELECT (created_at AT TIME ZONE 'UTC')::date AS day, count(*) AS conversations,
      count(*) FILTER (WHERE status = 'resolved') AS resolved,
      count(*) FILTER (WHERE status = 'handed_off') AS handed_off
    FROM assistant_conversations WHERE organization_id = :org AND created_at >= :since
    GROUP BY 1
    """
)
_SELF_SERVICE_LATENCY = text(
    """
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50
    FROM ai_runs
    WHERE organization_id = :org AND created_at >= :since AND kind = 'self_service'
    """
)


_PRIORITY_ORDER = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


def _minutes(value: timedelta | None) -> float | None:
    return round(value.total_seconds() / 60, 1) if value is not None else None


def _window(days: int) -> dict[str, Any]:
    since_day = datetime.now(UTC).date() - timedelta(days=days - 1)
    return {"since": datetime.combine(since_day, datetime.min.time(), tzinfo=UTC)}


def _days(params: dict[str, Any], days: int) -> list[date]:
    first = params["since"].date()
    return [first + timedelta(days=i) for i in range(days)]


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _rows(self, query: Any, params: dict[str, Any]) -> list[Any]:
        return list((await self.session.execute(query, params)).all())

    async def _daily_tickets(self, params: dict[str, Any], days: int) -> list[dict[str, Any]]:
        created = {r.day: r.n for r in await self._rows(_DAILY_CREATED, params)}
        resolved = {r.day: r.n for r in await self._rows(_DAILY_RESOLVED, params)}
        return [
            {"day": d.isoformat(), "created": created.get(d, 0), "resolved": resolved.get(d, 0)}
            for d in _days(params, days)
        ]

    async def overview(self, member: OrganizationMember, days: int) -> dict[str, Any]:
        require_role(member, *STAFF_ROLES)
        params = {"org": member.organization_id, **_window(days)}
        totals = (await self._rows(_TICKETS, params))[0]
        response = (await self._rows(_RESPONSE_TIMES, params))[0]
        resolution = (await self._rows(_RESOLUTION, params))[0]
        counts = (await self._rows(_COUNTS, params))[0]
        return {
            "days": days,
            "tickets": {
                "open": totals.open,
                "unassigned": totals.unassigned,
                "urgent": totals.urgent,
                "created": totals.created,
                "resolved": totals.resolved,
            },
            "open_by_status": {r.key: r.n for r in await self._rows(_BY, params)},
            "daily": await self._daily_tickets(params, days),
            "median_first_response_minutes": _minutes(response.first_response),
            "tickets_replied": response.replied,
            "median_resolution_minutes": _minutes(resolution.resolution),
            "customers": counts.customers,
            "indexed_documents": counts.documents,
            "sla": await self._sla(params),
        }

    async def _sla(self, params: dict[str, Any]) -> dict[str, Any]:
        rows = {r.kind: r for r in await self._rows(_SLA, params)}
        configured = (await self._rows(_SLA_CONFIGURED, params))[0].configured

        def compliance(kind: str) -> dict[str, Any]:
            r = rows.get(kind)
            counts = {
                key: getattr(r, key) if r else 0
                for key in ("on_time", "late", "overdue", "open", "cancelled")
            }
            decided = counts["on_time"] + counts["late"] + counts["overdue"]
            return {**counts, "rate": counts["on_time"] / decided if decided else None}

        return {
            "configured": configured,
            "first_response": compliance("first_response"),
            "resolution": compliance("resolution"),
            "reopened_cycles": rows["resolution"].reopened if "resolution" in rows else 0,
        }

    async def sla_detail(self, member: OrganizationMember, days: int) -> dict[str, Any]:
        """Service levels in more depth: compliance by priority, team and agent, the
        median business time to meet each target, due-soon and breached transitions
        per day, and why clocks were paused."""
        require_role(member, *STAFF_ROLES)
        params = {"org": member.organization_id, **_window(days)}
        teams = {r.id: r.name for r in await self._rows(_TEAM_NAMES, params)}
        people = {r.id: r.name for r in await self._rows(_STAFF_NAMES, params)}

        def label(group: str, key: Any) -> str:
            if group == "priority":
                return str(key)
            if key is None:
                return "No team" if group == "team" else "Unassigned"
            names = teams if group == "team" else people
            return names.get(key, "Removed")

        breakdowns: dict[str, list[dict[str, Any]]] = {}
        for group, query in _SLA_GROUPS.items():
            rows: dict[Any, dict[str, Any]] = {}
            for r in await self._rows(query, params):
                entry = rows.setdefault(r.key, {"key": str(r.key) if r.key else None})
                counts = {
                    k: getattr(r, k) for k in ("on_time", "late", "overdue", "open", "cancelled")
                }
                decided = counts["on_time"] + counts["late"] + counts["overdue"]
                entry[r.kind] = {**counts, "rate": counts["on_time"] / decided if decided else None}
            empty = {k: 0 for k in ("on_time", "late", "overdue", "open", "cancelled")}
            breakdowns[group] = sorted(
                (
                    {
                        "key": entry["key"],
                        "label": label(group, key),
                        "first_response": entry.get("first_response", {**empty, "rate": None}),
                        "resolution": entry.get("resolution", {**empty, "rate": None}),
                    }
                    for key, entry in rows.items()
                ),
                key=lambda e: (
                    (_PRIORITY_ORDER.get(e["key"], 9), e["label"].lower())
                    if group == "priority"
                    else (e["key"] is None, e["label"].lower())
                ),
            )

        business: dict[str, list[int]] = {"first_response": [], "resolution": []}
        calendars: dict[str, Any] = {}
        for r in await self._rows(_MET_CLOCKS, params):
            key = json.dumps(r.calendar_snapshot, sort_keys=True)
            if key not in calendars:
                calendars[key] = calendar_from_snapshot(r.calendar_snapshot)
            elapsed = business_minutes_between(r.started_at, r.met_at, calendars[key])
            business[r.kind].append(max(elapsed - r.paused_minutes, 0))

        daily = {d.isoformat(): {"at_risk": 0, "breached": 0} for d in _days(params, days)}
        for r in await self._rows(_SLA_DAILY, params):
            if r.day.isoformat() in daily:
                daily[r.day.isoformat()][r.kind] = r.n

        return {
            "days": days,
            "by_priority": breakdowns["priority"],
            "by_team": breakdowns["team"],
            "by_agent": breakdowns["agent"],
            "median_business_minutes": {
                kind: (statistics.median(values) if values else None)
                for kind, values in business.items()
            },
            "met": {kind: len(values) for kind, values in business.items()},
            "daily": [{"day": day, **counts} for day, counts in daily.items()],
            "pauses": [
                {
                    "reason": r.reason,
                    "count": r.n,
                    "ongoing": r.ongoing,
                    "median_minutes": _minutes(r.median),
                }
                for r in await self._rows(_PAUSES, params)
            ],
        }

    async def triage(self, member: OrganizationMember, days: int) -> dict[str, Any]:
        """AI triage in the period: how often it was confident (coverage), and how
        often people agreed with it, for confident predictions and, separately, for
        the top guess when it was unsure (what abstaining avoided)."""
        require_role(member, *STAFF_ROLES)
        params = {"org": member.organization_id, **_window(days)}
        counts = {"confident": 0, "unsure": 0, "failed": 0}
        applied = 0
        reviewed = {"confident": 0, "unsure": 0}
        correct = {"confident": 0, "unsure": 0}
        for r in await self._rows(_TRIAGE, params):
            counts[r.decision] += r.n
            applied += r.applied
            if r.outcome is not None and r.decision in reviewed:
                reviewed[r.decision] += r.n
                correct[r.decision] += r.n if r.outcome == "confirmed" else 0
        total = sum(counts.values())
        latency = (await self._rows(_TRIAGE_LATENCY, params))[0].p50
        settings = OrganizationSettings.model_validate(member.organization.settings or {})

        def rate(numerator: int, denominator: int) -> float | None:
            return numerator / denominator if denominator else None

        return {
            "days": days,
            "mode": settings.triage_mode,
            "predictions": total,
            **counts,
            "applied": applied,
            "coverage": rate(counts["confident"], total),
            "confident_reviewed": reviewed["confident"],
            "confident_correct": correct["confident"],
            "accuracy": rate(correct["confident"], reviewed["confident"]),
            "unsure_reviewed": reviewed["unsure"],
            "unsure_correct": correct["unsure"],
            "unsure_accuracy": rate(correct["unsure"], reviewed["unsure"]),
            "median_latency_ms": round(latency) if latency is not None else None,
            "by_category": [
                {
                    "key": r.key,
                    "name": r.name,
                    "predicted": r.predicted,
                    "reviewed": r.reviewed,
                    "correct": r.correct,
                    "accuracy": rate(r.correct, r.reviewed),
                }
                for r in await self._rows(_TRIAGE_CATEGORIES, params)
            ],
        }

    async def self_service(self, member: OrganizationMember, days: int) -> dict[str, Any]:
        """The customer assistant in the period (Module 12): how conversations ended,
        what its replies were, why it couldn't answer, and the questions it missed."""
        require_role(member, *STAFF_ROLES)
        params = {"org": member.organization_id, **_window(days)}
        totals = (await self._rows(_SELF_SERVICE, params))[0]
        outcomes = {"answered": 0, "clarify": 0, "unable": 0}
        reasons: dict[str, int] = {}
        rated = helpful = 0
        for r in await self._rows(_SELF_SERVICE_REPLIES, params):
            outcomes[r.outcome] += r.n
            rated += r.rated
            helpful += r.helpful
            if r.outcome == "unable":
                key = r.reason or "unknown"
                reasons[key] = reasons.get(key, 0) + r.n
        replies = sum(outcomes.values())
        daily = {r.day: r for r in await self._rows(_SELF_SERVICE_DAILY, params)}
        latency = (await self._rows(_SELF_SERVICE_LATENCY, params))[0].p50
        settings = OrganizationSettings.model_validate(member.organization.settings or {})

        def rate(numerator: int, denominator: int) -> float | None:
            return numerator / denominator if denominator else None

        return {
            "days": days,
            "mode": settings.self_service,
            "conversations": totals.conversations,
            "resolved": totals.resolved,
            "handed_off": totals.handed_off,
            "open": totals.open,
            "resolution_rate": rate(totals.resolved, totals.conversations),
            "handoff_rate": rate(totals.handed_off, totals.conversations),
            "replies": replies,
            **outcomes,
            "answer_rate": rate(outcomes["answered"], replies),
            "rated": rated,
            "helpful": helpful,
            "helpful_rate": rate(helpful, rated),
            "median_latency_ms": round(latency) if latency is not None else None,
            "reasons": [
                {"reason": reason, "replies": n}
                for reason, n in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
            ],
            "unanswered": [
                dict(r._mapping) for r in await self._rows(_SELF_SERVICE_UNANSWERED, params)
            ],
            "daily": [
                {
                    "day": d.isoformat(),
                    "conversations": daily[d].conversations if d in daily else 0,
                    "resolved": daily[d].resolved if d in daily else 0,
                    "handed_off": daily[d].handed_off if d in daily else 0,
                }
                for d in _days(params, days)
            ],
        }

    async def routing(self, member: OrganizationMember, days: int) -> dict[str, Any]:
        """How routing did in the period: its decisions, the share of tickets it
        assigned and how fast, how often people changed its choices, shadow-mode
        agreement, and each agent's workload."""
        require_role(member, *STAFF_ROLES)
        params = {"org": member.organization_id, **_window(days)}
        outcomes = {"assigned": 0, "team_only": 0, "no_agent": 0, "no_rule": 0}
        applied = dict(outcomes)
        shadow = dict(outcomes)
        for r in await self._rows(_ROUTING_DECISIONS, params):
            (applied if r.applied else shadow)[r.outcome] += r.n
        first = (await self._rows(_FIRST_ASSIGNMENTS, params))[0]
        overrides = (await self._rows(_OVERRIDES, params))[0]
        agreement = (await self._rows(_SHADOW, params))[0]
        workload = await self._rows(
            _WORKLOAD, {**params, "default_capacity": DEFAULT_MAX_ACTIVE_TICKETS}
        )
        settings = OrganizationSettings.model_validate(member.organization.settings or {})
        return {
            "days": days,
            "mode": settings.routing_mode,
            "decisions": {
                "total": sum(applied.values()),
                "by_outcome": applied,
                "shadow_total": sum(shadow.values()),
                "shadow_by_outcome": shadow,
            },
            "assignment": {
                "tickets_assigned": first.assigned,
                "automatic": first.automatic,
                "manual": first.assigned - first.automatic,
                "automatic_rate": first.automatic / first.assigned if first.assigned else None,
                "median_minutes": _minutes(first.wait),
                "median_minutes_automatic": _minutes(first.wait_automatic),
                "median_minutes_manual": _minutes(first.wait_manual),
            },
            "overrides": {
                "automatic": overrides.automatic,
                "changed_within_hour": overrides.changed,
                "rate": overrides.changed / overrides.automatic if overrides.automatic else None,
            },
            "shadow": {
                "decisions": agreement.decided,
                "compared": agreement.compared,
                "agreed": agreement.agreed,
                "rate": agreement.agreed / agreement.compared if agreement.compared else None,
            },
            "workload": [
                {
                    "user_id": r.user_id,
                    "name": r.name,
                    "active_tickets": r.active,
                    "max_active_tickets": r.capacity,
                    "assigned_automatically": r.automatic,
                    "assigned_by_people": r.manual,
                }
                for r in workload
            ],
        }

    async def ai_performance(self, member: OrganizationMember, days: int) -> dict[str, Any]:
        require_role(member, *STAFF_ROLES)
        params = {"org": member.organization_id, **_window(days)}

        daily: dict[str, dict[str, int]] = {d.isoformat(): {} for d in _days(params, days)}
        for r in await self._rows(_RUNS_DAILY, params):
            if r.day.isoformat() in daily:
                daily[r.day.isoformat()][r.kind] = r.n
        statuses: dict[str, dict[str, int]] = {}
        for r in await self._rows(_STATUSES, params):
            statuses.setdefault(r.kind, {})[r.status] = r.n
        answers = {
            status: sum(by_kind.get(status, 0) for by_kind in statuses.values())
            for status in {s for by_kind in statuses.values() for s in by_kind}
        }
        total = sum(answers.values())
        answered = answers.get("answered", 0) + answers.get("uncited", 0)
        human = answers.get("escalated", 0) + answers.get("abstained", 0) + answers.get("failed", 0)
        feedback = {r.rating: r.n for r in await self._rows(_FEEDBACK, params)}
        rated = sum(feedback.values())
        return {
            "days": days,
            "daily_runs": [{"day": day, **kinds} for day, kinds in daily.items()],
            "answers": {
                "total": total,
                "by_status": answers,
                "by_kind": statuses,
                "answer_rate": round(answered / total, 4) if total else None,
                "human_review_rate": round(human / total, 4) if total else None,
            },
            "decisions": [
                {
                    "decision": r.decision,
                    "reason": r.reason,
                    "count": r.n,
                    "mean_score": round(r.score, 4) if r.score is not None else None,
                }
                for r in await self._rows(_REASONS, params)
            ],
            "latency": {
                r.kind: {"p50_ms": round(r.p50, 1), "p95_ms": round(r.p95, 1), "runs": r.n}
                for r in await self._rows(_LATENCY, params)
            },
            "feedback": {
                "helpful": feedback.get("helpful", 0),
                "not_helpful": feedback.get("not_helpful", 0),
                "helpful_rate": round(feedback.get("helpful", 0) / rated, 4) if rated else None,
            },
            "models": [
                {
                    "provider": r.provider,
                    "model": r.model,
                    "runs": r.n,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                }
                for r in await self._rows(_TOKENS, params)
            ],
            "knowledge_gaps": [
                {
                    "query": r.query,
                    "count": r.n,
                    "last_asked": r.last_asked.isoformat(),
                    "run_id": str(r.run_id),
                    "status": r.status,
                }
                for r in await self._rows(_GAPS, params)
            ],
        }
