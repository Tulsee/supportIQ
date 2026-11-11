"""Evaluation datasets: labelled questions (JSON Lines) over a folder of documents.

Each question is labelled with the action an ideal assistant takes, the evidence
that supports the answer (exact quotes, so labels survive changes to chunking),
and key facts a correct answer must state. See datasets/README.md.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args

Category = Literal[
    "factual",
    "paraphrase",
    "identifier",
    "multi_part",
    "ambiguous",
    "conflicting",
    "insufficient",
    "unanswerable",
]
Action = Literal["answer", "clarify", "escalate", "abstain"]
Split = Literal["dev", "test"]

# The action each category calls for (see the decision policy in the plan, Module 5).
EXPECTED_ACTION: dict[str, Action] = {
    "factual": "answer",
    "paraphrase": "answer",
    "identifier": "answer",
    "multi_part": "answer",
    "ambiguous": "clarify",
    "conflicting": "escalate",
    "insufficient": "abstain",
    "unanswerable": "abstain",
}

SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".pdf", ".docx")


class DatasetError(ValueError):
    pass


@dataclass(frozen=True)
class Evidence:
    document: str  # file name in the documents folder
    quote: str  # exact text from the document


@dataclass(frozen=True)
class Question:
    id: str
    category: Category
    expected_action: Action
    split: Split
    question: str
    evidence: list[Evidence] = field(default_factory=list)
    # Each inner list is one fact; any of its alternatives counts.
    key_facts: list[list[str]] = field(default_factory=list)
    reference_answer: str = ""
    notes: str = ""

    @property
    def answerable(self) -> bool:
        return self.expected_action == "answer"


@dataclass(frozen=True)
class Dataset:
    name: str
    path: Path
    sha256: str
    questions: list[Question]

    def select(
        self, *, split: str | None = None, ids: set[str] | None = None, limit: int | None = None
    ) -> list[Question]:
        chosen = [
            q
            for q in self.questions
            if (split in (None, "all") or q.split == split) and (not ids or q.id in ids)
        ]
        return chosen[:limit] if limit else chosen


def _question(row: dict, line: int) -> Question:
    def need(name: str) -> object:
        if name not in row:
            raise DatasetError(f"line {line}: missing {name!r}")
        return row[name]

    category = need("category")
    if category not in get_args(Category):
        raise DatasetError(f"line {line}: unknown category {category!r}")
    action = need("expected_action")
    if action != EXPECTED_ACTION[str(category)]:
        raise DatasetError(
            f"line {line}: {category} questions expect {EXPECTED_ACTION[str(category)]!r}"
        )
    split = need("split")
    if split not in get_args(Split):
        raise DatasetError(f"line {line}: unknown split {split!r}")
    evidence = [Evidence(e["document"], e["quote"]) for e in row.get("evidence", [])]
    key_facts = [[k] if isinstance(k, str) else list(k) for k in row.get("key_facts", [])]
    if action == "answer" and not (evidence and key_facts):
        raise DatasetError(f"line {line}: answerable questions need evidence and key facts")
    return Question(
        id=str(need("id")),
        category=category,  # type: ignore[arg-type]
        expected_action=action,  # type: ignore[arg-type]
        split=split,  # type: ignore[arg-type]
        question=str(need("question")),
        evidence=evidence,
        key_facts=key_facts,
        reference_answer=row.get("reference_answer", ""),
        notes=row.get("notes", ""),
    )


def load_dataset(path: Path) -> Dataset:
    raw = path.read_bytes()
    questions: list[Question] = []
    for line, text in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if text.strip():
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"line {line}: {exc}") from exc
            questions.append(_question(row, line))
    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        raise DatasetError("question ids must be unique")
    return Dataset(
        name=path.stem,
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        questions=questions,
    )


def document_files(folder: Path) -> list[Path]:
    """The documents of a corpus, in a stable order."""
    return sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
