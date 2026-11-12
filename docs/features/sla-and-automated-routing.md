# SLA management and automated ticket routing

**Status:** planned (Module 10 in the [implementation plan](../../IMPLEMENTATION_PLAN.MD#15-phase-2-feature-roadmap)).
**Source:** the feature plan "SupportIQ - SLA Management & Automated Ticket Routing",
adapted to this repository after its Phase 0 audit (12 November 2026). Where this
document and the original differ, this one records the deviation and the reason.

The goal is unchanged: configurable first-response and resolution SLAs measured in
business time, and deterministic, explainable assignment of tickets to teams and
agents, with a safe unassigned fallback. Not in scope: predictive breach models,
LLM-chosen assignees, workforce scheduling, cross-tenant assignment.

## 1. Phase 0 audit: what exists

| The plan assumes | In SupportIQ today | Consequence |
| --- | --- | --- |
| Ticket priority | `low`, `normal`, `high`, `urgent` (`TicketPriority`) | Use these; the plan's "medium" is `normal` |
| Ticket status lifecycle | `open → assigned → in_progress ⇄ waiting_for_customer`, `in_progress → resolved → closed`, `resolved → in_progress` on reopen (customer reply or staff) | Hooks go into `TicketService._set_status` |
| Assignee and assignment history | `tickets.assignee_id` + `ticket_events` (`assigned`/`unassigned` with from/to snapshots) | Reuse; no `ticket_assignments` table (see deviations) |
| Ticket category | **Missing** | Add organization-defined categories (Phase 1) |
| Language, product, customer tier | Missing; customers have a free-form `metadata` JSONB | Rules may read allow-listed customer metadata keys; language detection comes with AI triage (Module 11) |
| Teams, skills, presence, capacity | **Missing** | Add (Phase 5) |
| Roles | Organization roles `admin`, `agent`, `customer`; any staff member can assign any ticket | Team lead is a *team* role, not a new organization role |
| Notifications | **Missing** (only invitation emails) | Add in-app notifications as a foundation (Module 9) |
| Audit log | `ticket_events` for tickets only; nothing for settings | Add a general `audit_log` (Module 9) |
| Periodic jobs | **No Celery beat**; maintenance runs as an asyncio loop inside the API | Add a beat schedule and a `scheduler` service |
| Outbox | None; jobs are enqueued after commit, with a sweep for missed work (ingestion) | Same pattern for routing (no outbox needed) |
| Real-time events | Redis pub/sub → WebSockets; events say *which* ticket changed, clients refetch | Add `ticket.sla.*` and assignment kinds to the same broker |
| First-response analytics | Module 7 measures the first public agent message | **Bug:** for tickets a staff member opens for a customer, the opening message is authored by an agent and counts as the "first response". Fix with the SLA work |
| Tenant isolation | PostgreSQL row-level security on every tenant table; least-privilege API role | Every new table gets RLS policies and grants in its migration |
| Time zones | No organization time zone | Calendars carry an IANA time zone; add `tzdata` as a direct dependency (Windows has no system zone database, and slim Docker images may not either) |

## 2. Deviations from the original plan

| Original plan | Here | Why |
| --- | --- | --- |
| Policies reference a calendar; several calendars | One default calendar per organization (the schema allows more); policies use it | Simpler setup and UI; per-team calendars can come with routing teams |
| Calendar CRUD endpoints | `GET /sla`, `POST /sla/setup` (defaults), `PUT /sla/calendar` (replaces the weekly hours), holiday add/remove, policy create/update, `GET /sla/preview` | One page edits everything; the preview makes the business-time rules visible |
| `backend/app/modules/sla/...`, `routing/...` | Pure logic in `backend/app/domain/` (`business_time.py`, `sla.py`, `routing.py`); persistence and orchestration in the existing `models/`, `repositories/`, `services/`, `api/v1/endpoints/`, `workers/` | Matches the repository's layering (as `ai_engine/` does for AI); domain code has no FastAPI, SQL or Redis imports |
| `ticket_assignments` table | Keep `tickets.assignee_id` (the one current assignment) and `ticket_events`; assignment events gain `source` (`manual`, `automatic`, `escalation`), `reason` and `decision_id` | The timeline already is the append-only assignment history; a second history would drift from it |
| Clocks unique per ticket and type | Unique per ticket, type **and cycle** | A reopened ticket starts a new resolution cycle without rewriting the first (decision 7) |
| Calendar referenced by ID only | Clocks store a `calendar_snapshot` (time zone, hours, holidays) | Deadlines are recomputed on resume and priority change; using the calendar *as it was* keeps history reproducible when an admin edits hours later |
| `SCREAMING_CASE` error codes | Lowercase snake case (`sla_policy_not_found`, `routing_rule_invalid`, `agent_not_eligible`, `assignment_conflict`) in the existing `{"error": {...}}` shape | Repository convention |
| Separate presence heartbeat endpoint | The app's existing ticket WebSocket refreshes a Redis key (`presence:{org}:{user}`, 90 s TTL); the agent's chosen status lives in the database | No extra polling; a closed tab expires on its own |
| Durable job outbox | Ticket and its clocks commit together; the routing job is enqueued after commit; a beat task routes `open` tickets with no decision after 2 minutes | Same reliability with the existing patterns |
| Team lead organization role | `team_members.role` = `member` or `lead` | Avoids changing organization RBAC |

## 3. Decisions (the plan's section 18, answered for this lifecycle)

1. **Resolution is met by `resolved`**, not `closed`. Tickets can also be closed without
   being resolved (staff: spam, duplicates; customers: their own requests they no longer
   need). That stops any unmet clock with the state `cancelled`, neither met nor
   breached, and compliance reports leave those tickets out with a stated count.
2. **Pausing:** `waiting_for_customer` pauses the *resolution* clock automatically (reason `waiting_for_customer`). The first-response clock never pauses: a ticket can only reach that status after an agent has handled it. Manual pauses (other reasons) are admin-only and audited.
3. **First response** is the first public message by staff **after** the opening message. Internal notes, customer messages and the opening message of a staff-created ticket don't count. Sending an AI-suggested reply counts (a person sent it).
4. **Capacity** counts tickets assigned to the agent in `assigned` or `in_progress`; `waiting_for_customer` doesn't consume capacity.
5. **Self-assignment and reassignment** keep today's rule (any staff member); manual assignment takes an optional reason and always wins over automatic routing.
6. **No eligible agent:** the ticket keeps its team (if a rule matched), stays `open` and unassigned, and appears in the unassigned queue with the routing explanation. Never assign an unavailable or full agent.
7. **Reopen** (`resolved → in_progress`) starts resolution cycle 2 with the same target, from the reopen time. Cycle 1 keeps its met time. Compliance reports the first cycle and, separately, reopened cycles.
8. **Priority change** re-targets clocks that aren't met yet: the deadline is recomputed from the original start with the new policy (pauses excluded). A breach already recorded stays recorded.

## 4. SLA design

### Policy semantics (unchanged from the plan)

Seed defaults, editable and not a guarantee:

| Priority | First response | Resolution |
| --- | ---: | ---: |
| Urgent | 1 business hour | 8 business hours |
| High | 2 business hours | 16 business hours |
| Normal | 4 business hours | 24 business hours |
| Low | 8 business hours | 40 business hours |

Seed calendar: Monday to Friday, 09:00-17:00, `Asia/Kathmandu` by default (editable), no
holidays. Warning window: 60 business minutes. Policy selection: the active policy for
the ticket's priority, else the organization's default policy, else **unconfigured** (no
deadline is invented; admins are notified once).

States: `on_track`, `at_risk`, `paused`, `breached`, `met`, `unconfigured`. A target met
after its deadline keeps `breached_at`: breach history is never erased.

### Business-time calculator (`app/domain/business_time.py`)

```python
def calculate_deadline(start: datetime, minutes: int, calendar: Calendar) -> datetime: ...
def business_minutes_between(start: datetime, end: datetime, calendar: Calendar) -> int: ...
def warning_instant(due: datetime, warning_minutes: int, calendar: Calendar) -> datetime: ...
```

Pure functions over a frozen `Calendar` value (IANA zone, weekday intervals, holiday
dates). Rules from the plan apply: aware datetimes only; arithmetic in the calendar's
zone with `zoneinfo`; starts outside working time move to the next interval; only
working minutes are consumed; several intervals per day; DST handled by converting each
interval boundary to UTC; negative targets and naive datetimes rejected; results
stored in UTC. Never `created_at + timedelta`.

### Data model

All tables have `organization_id`, staff-only RLS (customers get no access), and
tenant-leading indexes.

| Table | Columns (beyond id, organization_id, timestamps) |
| --- | --- |
| `business_calendars` | `name`, `timezone`, `is_default`, `is_active` |
| `business_hours` | `calendar_id`, `weekday` (1-7), `start_time`, `end_time`; CHECK start < end; no overlaps (validated in the service) |
| `business_holidays` | `calendar_id`, `date`, `name`; unique (calendar, date) |
| `sla_policies` | `name`, `priority` (null = organization default), `calendar_id`, `first_response_minutes`, `resolution_minutes`, `warning_minutes`, `is_active`, `version`, `archived_at`; partial unique index on active (organization, priority) |
| `ticket_sla_clocks` | `ticket_id`, `kind` (`first_response`, `resolution`), `cycle`, `policy_id`, `policy_version`, `target_minutes`, `warning_minutes`, `calendar_snapshot` (JSONB), `started_at`, `warn_at`, `due_at`, `state`, `at_risk_at`, `breached_at`, `met_at`, `paused_since`; unique (ticket, kind, cycle); index (organization, state, due_at) and (state, warn_at) |
| `ticket_sla_pauses` | `clock_id`, `reason`, `source` (`automatic`, `manual`), `actor_id`, `started_at`, `ended_at` |

New `ticket_events` types (staff only): `sla_at_risk`, `sla_breached`, `sla_paused`,
`sla_resumed`, `team_changed`. The timeline then shows SLA history next to assignments.

### Lifecycle hooks (in `TicketService`)

| Where | What |
| --- | --- |
| `create` | In the same transaction: resolve the policy, compute both clocks from `created_at`, insert them (or mark `unconfigured`). After commit: enqueue routing |
| `add_message` | Staff, public, not the opening message → set the open first-response clock's `met_at` once (idempotent: `WHERE met_at IS NULL`) |
| `_set_status` | → `resolved`: resolution met. → `waiting_for_customer`: start an automatic pause. Leaving it: end the pause, recompute `due_at` and `warn_at`. `resolved → in_progress`: new resolution cycle |
| priority change | Re-target unmet clocks (decision 8) |
| after commit | Publish `ticket.updated` so open views refetch the SLA panel |

### Monitoring (Celery beat, every minute)

`evaluate_sla_clocks`: select clocks in `on_track`/`at_risk` with `warn_at <= now()` or
`due_at <= now()`, ordered by `due_at`, `LIMIT 500 FOR UPDATE SKIP LOCKED` (so two
schedulers can't double-process), apply the transition, record the ticket event, and
create notifications with a dedupe key (`sla:{clock_id}:{state}`). Notifications and
real-time events go out after commit; their failure never rolls back a state change.

| Event | Notified |
| --- | --- |
| First response at risk | Assignee, or the team's leads if unassigned |
| Resolution at risk | Assignee and team leads |
| Breach (either) | Team leads and admins; escalation recorded in the timeline |
| Unconfigured SLA | Admins, once per organization per day |

## 5. Routing design

### Teams and agents

| Table | Columns |
| --- | --- |
| `ticket_categories` | `key`, `name`, `description`, `is_active`; `tickets.category_id` (nullable) |
| `teams` | `name`, `description`, `is_active`; `tickets.team_id` (nullable) |
| `team_members` | `team_id`, `user_id`, `role` (`member`, `lead`), `is_active`; unique (team, user) |
| `agent_profiles` | `user_id`, `status` (`available`, `busy`, `away`, `offline`), `max_active_tickets` (default 10), `last_assigned_at` |
| `agent_skills` | `user_id`, `skill` (slug), `level` (1-5); unique (organization, user, skill) |

**Eligible** = staff member of the organization, active member of the target team,
status `available`, presence key fresh in Redis, every required skill at or above its
level, and active workload (decision 4) below capacity.

### Rules

`routing_rules`: `name`, `position`, `conditions` (JSONB), `team_id`, `required_skills`
(`[{"skill": "refunds", "level": 3}]`), `strategy` (`least_loaded`, `round_robin`,
`skill_match`, `manual`), `is_active`, `version`.

Conditions use the plan's constrained format (`all` / `any` lists of
`{field, operator, value}`), validated by Pydantic. Allowed fields: `category`,
`priority`, `subject` (operator `contains` only), `customer.<key>` for keys an admin
allow-lists, and later `language` (Module 11). Operators: `eq`, `in`, `contains`,
`is_set`. No expressions, no `eval`, no dynamic SQL. The first active rule in position
order wins.

### Engine (`app/domain/routing.py` + `services/routing.py`)

1. Load the ticket (`FOR UPDATE`). Stop if it already has an assignee (manual wins), is
   not `open`, or a decision with the same job key exists (idempotency: key =
   ticket + trigger + ticket `updated_at`).
2. Match a rule; none → outcome `unassigned`.
3. Set the team; strategy `manual` → outcome `team_only`.
4. Build candidates with an exclusion reason for each agent who fails a check.
5. `least_loaded` (default): lowest active/capacity ratio; ties → oldest
   `last_assigned_at`, then user ID. `round_robin`: oldest `last_assigned_at`.
   `skill_match`: highest summed skill level, then least loaded.
6. Lock the chosen agent's profile row, re-count their workload, and assign through
   `TicketService._assign` (event `source: automatic`), or fall back to `team_only`.
7. Persist a `routing_decisions` row: trigger, rule, strategy, outcome, candidates with
   scores and exclusions, and a one-paragraph explanation. No message content.

Triggers: ticket created (job enqueued after commit), a sweep for `open` tickets
without a decision, manual "Reroute", and later "agent became available". A dry-run
**simulator** runs steps 2-5 on a hypothetical ticket and writes nothing.

## 6. API

Same conventions as today: `/api/v1`, `X-Organization-Id`, staff-only unless stated.

| Method and path | Purpose | Who |
| --- | --- | --- |
| `GET/POST /sla/policies`, `PATCH /sla/policies/{id}`, `POST .../archive` | Policies | admin (GET: staff) |
| `GET/POST /sla/calendars`, `PATCH /sla/calendars/{id}`, `PUT .../hours`, `POST/DELETE .../holidays` | Calendars | admin |
| `GET /tickets/{id}/sla` | Clocks, pauses, states | staff |
| `POST /tickets/{id}/sla/pause`, `.../resume` | Manual pause | admin |
| `GET/POST /categories`, `PATCH /categories/{id}` | Categories | admin |
| `GET/POST /teams`, `PATCH /teams/{id}`, `PUT /teams/{id}/members` | Teams | admin |
| `GET/PUT /agents/{user_id}/profile` | Capacity and skills | admin |
| `PUT /me/presence` | Own status | staff |
| `GET/POST /routing/rules`, `PATCH /routing/rules/{id}`, `PUT /routing/rules/order` | Rules | admin |
| `POST /routing/simulate` | Dry run | admin |
| `GET /tickets/{id}/routing` | Decisions and explanation | staff |
| `POST /tickets/{id}/reroute` | Run routing again | staff |
| `GET /tickets?sla=at_risk\|breached&team=...&unassigned=true` | Queue filters on the existing list | staff |

## 7. Front end

- **Settings → Service levels:** policies per priority, the business calendar (time
  zone, weekly hours, holidays) with a "next working time" preview.
- **Settings → Teams and routing:** teams and members (with leads), agent capacity and
  skills, ordered rules with a constrained condition builder, and the simulator.
- **Settings → Categories.**
- **Ticket list:** SLA badge (text and color: "On track", "Due in 35 min", "Breached",
  "Paused", "Met", "No SLA"), team column, filters for at risk, breached, unassigned and
  my team.
- **Ticket detail:** SLA panel with both clocks and remaining business time (server
  values; the countdown is display only), team, assignment explanation, reroute and
  reassign-with-reason actions; SLA and routing events in the timeline.
- **App shell:** notification bell (Module 9) and a presence switch in the user menu.
- **Analytics:** SLA compliance and routing sections (section 9).

## 8. Real-time events and audit

Broker events (notifications only, as today): `ticket.updated` for clock and assignment
changes, `notification.created` per user. Settings changes (policies, calendars, rules,
teams, skills, manual pauses) are written to the `audit_log` (Module 9) with actor and a
minimal before/after.

## 9. Analytics

- First-response and resolution compliance by priority, team and agent, with
  denominators stated: met on time / (met + breached); open tickets past due counted as
  breached; open tickets within target shown separately as "in progress", never as
  compliant.
- Median business time to first response and to resolution.
- At-risk and breached counts over time; pause reasons.
- Routing: automatic, team-only, unassigned and failed rates; manual override rate
  (automatic assignments changed by a person within an hour); median time to
  assignment; workload distribution per agent.

## 10. Testing

Everything in the plan's section 15, plus:

- Business-time tests with fixed instants in `Asia/Kathmandu` (UTC+05:45, a non-hour
  offset) and a DST zone (`Europe/London`), across weekends, holidays, interval edges.
- The opening message of a staff-created ticket doesn't meet first response; a sent AI
  suggestion does.
- `waiting_for_customer` pauses and resumes the resolution clock; reopen starts cycle 2.
- RLS: customers can't read clocks, decisions, teams or rules; another organization
  can't read or write any of them (direct SQL as `supportiq_app`, like the existing RLS
  tests).
- Concurrency: two routing jobs for one ticket create one assignment; two tickets
  routed at once don't push an agent past capacity; a manual assignment between job
  enqueue and run wins.
- Two scheduler instances process each clock transition once.

## 11. Phases

| Phase | Scope | Estimate |
| --- | --- | --- |
| 0 | Repository audit (this document) | done |
| 1 | Foundations: notifications and audit log (Module 9), Celery beat + `scheduler` service, categories, organization settings (done; also auto-close); `tzdata` moves to phase 2 | 3-4 days |
| 2 | SLA configuration: calendars, policies, business-time calculator and its tests, seed defaults (done) | 2-3 days |
| 3 | Ticket clocks: lifecycle hooks, pauses, cycles, `GET /tickets/{id}/sla`, first-response analytics fix | 2-3 days |
| 4 | Monitoring: evaluator, notifications, escalation, timeline events | 2 days |
| 5 | Teams, members, agent profiles, skills, presence | 2-3 days |
| 6 | Routing: rules, engine, jobs and sweep, decisions, reroute, simulator | 3-4 days |
| 7 | Front end: settings, queue and ticket panels, bell, presence | 4-5 days |
| 8 | Analytics, load and concurrency tests, docs, screenshots, demo data | 2-3 days |

About 20-27 working days for one developer.

## 12. Rollout

As in the original: (A) clocks computed, visible to admins only; (B) visible to agents
with at-risk alerts, routing manual; (C) routing in shadow mode (decisions recorded and
compared with what people did, no assignment); (D) automatic routing for one team or
category; (E) per-organization switches (a new `settings` JSONB column on
`organizations`, with `sla_enabled` and `auto_routing_enabled`), so automatic routing
can be turned off without affecting ticket creation.

## 13. Acceptance criteria

The original's section 17 applies unchanged, plus: SLA and routing data are invisible
to customers at the database level (RLS), and the existing ticketing, AI and evaluation
test suites still pass.
