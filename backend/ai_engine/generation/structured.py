"""The structured answer: the model reports what kind of answer it could give.

With the assessed decision policy the model replies with JSON, so "the passages
don't say", "the passages disagree" and "the question could mean several things"
are explicit instead of buried in prose. These self-reports are one signal among
several (retrieval relevance, coverage, claim checks); none decides alone.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

ModelStatus = Literal[
    "answered", "partial", "not_found", "conflicting", "ambiguous", "unstructured"
]

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["answered", "partial", "not_found", "conflicting", "ambiguous"],
        },
        "answer": {"type": "string"},
        "conflicting_passages": {"type": "array", "items": {"type": "integer"}},
        "clarifying_question": {"type": "string"},
    },
    "required": ["status", "answer", "conflicting_passages", "clarifying_question"],
    "additionalProperties": False,
}

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class StructuredAnswer:
    status: ModelStatus
    answer: str
    conflicting_passages: list[int] = field(default_factory=list)
    clarifying_question: str = ""


def parse_structured(raw: str) -> StructuredAnswer:
    """The model's JSON reply, or its text as an ``unstructured`` answer when the
    reply isn't valid JSON (a model without structured output support)."""
    text = _FENCE.sub("", raw.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return StructuredAnswer("unstructured", raw.strip())
    if not isinstance(data, dict) or data.get("status") not in get_statuses():
        return StructuredAnswer("unstructured", raw.strip())
    passages = data.get("conflicting_passages") or []
    return StructuredAnswer(
        status=data["status"],
        answer=str(data.get("answer") or "").strip(),
        conflicting_passages=[int(n) for n in passages if isinstance(n, int | float)],
        clarifying_question=str(data.get("clarifying_question") or "").strip(),
    )


def get_statuses() -> list[str]:
    return list(ANSWER_SCHEMA["properties"]["status"]["enum"])
