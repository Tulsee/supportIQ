"""Conversations: follow-up questions, ticket threads, and ticket summaries.

Retrieval needs a self-contained query. A follow-up like "and for the camera?" or
"how long does it take?" means little alone, so it is searched together with the
question before it. A ticket's query is its subject plus the latest customer
messages. No model call is needed for either.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from ai_engine.evidence.signals import content_terms, is_specific, vagueness
from ai_engine.generation.llm import Prompt

MAX_QUERY_CHARS = 500
_FOLLOW_UP_STARTS = ("and ", "also ", "what about", "how about", "then ", "but ", "so ")


@dataclass(frozen=True)
class Turn:
    question: str
    answer: str


def is_follow_up(question: str) -> bool:
    """Short, starts like a continuation, or has no specific subject of its own."""
    lowered = question.strip().lower()
    if lowered.startswith(_FOLLOW_UP_STARTS):
        return True
    if vagueness(question)[0]:
        return True
    return not is_specific(question) and len(content_terms(question)) <= 2


def contextual_query(question: str, history: Sequence[Turn]) -> str:
    """The query to search for: the question, joined with the previous one when it
    is a follow-up."""
    if not history or not is_follow_up(question):
        return question
    combined = f"{history[-1].question.strip()} {question.strip()}"
    return combined[-MAX_QUERY_CHARS:]


def history_block(history: Sequence[Turn], *, max_turns: int = 3) -> str:
    """Earlier turns, for the prompt (the model reads follow-ups in context)."""
    if not history:
        return ""
    lines = []
    for turn in history[-max_turns:]:
        lines.append(f"Q: {turn.question.strip()}")
        lines.append(f"A: {turn.answer.strip()[:600]}")
    return "<conversation>\n" + "\n".join(lines) + "\n</conversation>\n\n"


@dataclass(frozen=True)
class ThreadMessage:
    author: str  # "customer" or "agent"
    body: str
    internal: bool = False


def ticket_query(subject: str, messages: Sequence[ThreadMessage]) -> str:
    """What the customer is asking: the latest customer message, with the subject
    (and the message before it when the latest one is a short follow-up)."""
    customer = [m.body.strip() for m in messages if m.author == "customer" and m.body.strip()]
    if not customer:
        return subject[:MAX_QUERY_CHARS]
    latest = customer[-1]
    parts = [subject.strip(), latest]
    if len(customer) > 1 and is_follow_up(latest):
        parts.insert(1, customer[-2])
    query = " ".join(p for p in parts if p)
    return query[:MAX_QUERY_CHARS]


SUMMARY_PROMPT_VERSION = "ticket-summary-v1"

SUMMARY_SYSTEM_PROMPT = """\
You summarize customer support tickets for the support agents who handle them.

Write 3 to 5 short bullet points, starting each with "- ":
- what the customer needs or the problem they report,
- what has been tried or answered so far,
- the current state and what is still open,
- the next step for the agent, if the conversation shows one.

Use only what the conversation says; do not add facts or guess. Internal notes are \
visible to agents only; you may use them. Plain text, no headings."""


def summary_prompt(
    subject: str,
    customer_name: str,
    status: str,
    messages: Sequence[ThreadMessage],
    *,
    max_chars: int = 12_000,
) -> Prompt:
    """The newest messages that fit ``max_chars`` (older ones are dropped first)."""
    rendered: list[str] = []
    used = 0
    for message in reversed(messages):
        who = "Customer" if message.author == "customer" else "Agent"
        if message.internal:
            who += " (internal note)"
        line = f"{who}: {message.body.strip()}"
        if used + len(line) > max_chars and rendered:
            rendered.append("[earlier messages omitted]")
            break
        rendered.append(line)
        used += len(line)
    thread = "\n\n".join(reversed(rendered))
    user = (
        f"Ticket subject: {subject}\nCustomer: {customer_name}\nStatus: {status}\n\n"
        f"<conversation>\n{thread}\n</conversation>"
    )
    return Prompt(system=SUMMARY_SYSTEM_PROMPT, user=user)
