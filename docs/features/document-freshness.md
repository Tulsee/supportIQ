# Document freshness

**Status:** implemented (Module 13, last part, in the
[implementation plan](../../IMPLEMENTATION_PLAN.MD#157-module-13---knowledge-base-quality-loop),
26 November 2026). With the [contradiction scanner](contradiction-scanner.md) and
[knowledge gaps to articles](knowledge-gaps.md), this completes the knowledge-base
quality loop. It is deterministic: no model, so there is no evaluation set.

Documents go stale quietly. The answer evaluation's most common escalation was an
outdated FAQ that disagreed with the current policy. Nobody had looked at the FAQ since
the policy changed. Freshness gives every document someone responsible for it and a date
to look at it again. It also shows which documents the assistant never uses.

## Owners and review dates

An admin can give a document:

- **An owner:** an active staff member (admin or agent) who keeps it current. Customers
  can't own documents, and someone who leaves the organization stops being reminded.
- **A review date:** when the document should next be checked.
- **An interval:** how far to move the review date after a review (every month, 3
  months, 6 months or year in the app; any 1-730 days through the API). Without one, a
  review clears the date.

**Mark reviewed** (the owner or an admin) records who reviewed it and when, then moves
the review date to today plus the interval. A new version doesn't count as a review: an
upload can change one paragraph and leave a stale one, so only a person confirms the
whole document is current.

Every change is in the audit log:

| Action | When |
| --- | --- |
| `document.owner_changed` | The owner is set, changed or removed |
| `document.review_scheduled` | The review date or the interval changes |
| `document.reviewed` | Someone marks it reviewed (with the next date) |

## Reminders

A periodic job (`documents.review_reminders`, hourly) finds documents whose review date
has come and sends a **Document reviews** notification:

| Situation | Who is reminded |
| --- | --- |
| The document has an owner who is still active staff here | The owner |
| No owner, or the owner left, lost staff access or was deactivated | Every admin |

- **One reminder per due date:** each due date reminds each person once, however often
  the job runs (the notification's dedupe key is the document and the date). Rescheduling
  to a new date makes a new reminder.
- **Delivery:** in the app by default; each person can switch it to email or off under
  their notification settings.
- **Opening it:** the reminder opens the document.
- **Time zone:** dates are compared in UTC, so a reminder arrives within an hour of
  midnight UTC on the due date (05:45 in Nepal).

## Citations

Each document shows how much the assistant uses it:

- **Cited in the last 90 days:** how many answers cited it.
- **Last cited:** when the most recent answer citing it was given.
- **Not cited:** flagged when the document has been indexed for at least 30 days and no
  answer cited it in the last 90.

**Which answers count:** only answers people were given, meaning the evidence policy
let the answer through with a citation, from any channel (the Ask page, ticket
suggestions, the customer assistant). Abstentions, escalations and clarifying questions
cite nothing. Evaluation probes and gap rechecks are runs with a `config.origin`, and are
left out. They aren't people's questions.

**What "not cited" means:** it marks a candidate for review, never for removal. A
document can be uncited because nobody asks about it, or because retrieval never finds
it, which is worth knowing too. Nothing is hidden or deleted automatically.

## Screens

- **Knowledge base list:** a **Freshness** filter (review due, not cited recently), and
  *Review due* and *Not cited* badges on each document.
- **A document's page:** a *Freshness* section under the details.
  - Admins choose the owner, the review date and the interval.
  - Everyone sees the last review, the citation counts and the flags.
  - The owner and admins have **Mark reviewed**.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `DOCUMENT_REVIEW_CHECK_INTERVAL_MINUTES` | 60 | How often the reminder job runs; 0 turns reminders off |
| `DOCUMENT_CITATION_WINDOW_DAYS` | 90 | Answers this recent count as citations |
| `DOCUMENT_NEW_DAYS` | 30 | A document is too new to flag as not cited until it has been indexed this long |

## API

| Method and path | Who | What |
| --- | --- | --- |
| `GET /documents?freshness=review_due\|not_cited` | staff | Documents whose review date has come, or that aren't cited recently |
| `GET /documents/{id}` | staff | Includes `owner`, `review_due_on`, `review_interval_days`, `review_due`, `reviewed_at`, `reviewed_by` and `citations` (`recent`, `last_cited_at`, `window_days`, `not_cited`) |
| `PATCH /documents/{id}` | admin | `{"owner_id", "review_due_on", "review_interval_days"}`; `null` removes one, a field left out stays. `400 invalid_owner` unless the owner is active staff here |
| `POST /documents/{id}/reviews` | owner or admin | Mark it reviewed |

## Limitations

- **Dates are in UTC.** The organization's business calendar has a time zone, but
  reminders don't use it yet.
- **No escalation of overdue reviews.** A document stays "review due" until someone
  reviews it, and its owner gets one reminder for that date. Overdue reviews are visible
  in the list (filter: review due), but nobody is reminded again.
- **Citation counts depend on traffic.** In a small organization, 90 days may hold too
  few questions for "not cited" to mean much. The window is a setting.
