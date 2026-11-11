"""The evaluation dataset is consistent with its documents.

Labels point at evidence by quoting it, so they must quote the documents exactly,
and each quote must land inside one indexed chunk (or retrieval scores would be
wrong for reasons that have nothing to do with retrieval)."""

from collections import Counter
from pathlib import Path

import pytest

from ai_engine.evaluation.dataset import (
    EXPECTED_ACTION,
    DatasetError,
    document_files,
    load_dataset,
)
from ai_engine.evaluation.metrics import normalize
from ai_engine.ingestion.extract import DocumentType, extract
from app.core.config import REPO_ROOT, settings

DATASET = REPO_ROOT / "datasets" / "evaluation_questions" / "quillon_support_v1.jsonl"
CORPUS = REPO_ROOT / "datasets" / "sample_documents" / "quillon"
# A section is chunked on its own; under this many words it fits in one chunk
# (BGE-small averages well under 1.5 tokens per English word).
MAX_SECTION_WORDS = int(settings.chunk_size_tokens / 1.5)


def paragraphs(path: Path) -> list[tuple[str | None, str]]:
    document = extract(
        DocumentType.MARKDOWN, path.read_bytes(), max_pages=10, max_characters=1_000_000
    )
    return [(b.heading, b.text) for b in document.blocks if not b.is_heading]


def test_the_dataset_loads_and_covers_every_category_in_both_splits() -> None:
    dataset = load_dataset(DATASET)

    assert len(dataset.questions) >= 50
    categories = Counter((q.category, q.split) for q in dataset.questions)
    for category in EXPECTED_ACTION:
        assert categories[(category, "dev")] >= 3, category
        assert categories[(category, "test")] >= 3, category
    assert all(q.expected_action == EXPECTED_ACTION[q.category] for q in dataset.questions)


def test_every_quote_is_inside_one_paragraph_of_its_document() -> None:
    dataset = load_dataset(DATASET)
    files = {path.name: path for path in document_files(CORPUS)}
    texts = {
        name: [normalize(text) for _, text in paragraphs(path)] for name, path in files.items()
    }

    missing = [
        f"{q.id}: {e.document}: {e.quote!r}"
        for q in dataset.questions
        for e in q.evidence
        if e.document not in texts or not any(normalize(e.quote) in p for p in texts[e.document])
    ]

    assert missing == []


def test_every_section_fits_in_one_chunk() -> None:
    for path in document_files(CORPUS):
        words: Counter[str | None] = Counter()
        for heading, text in paragraphs(path):
            words[heading] += len(text.split())
        too_long = {heading: n for heading, n in words.items() if n > MAX_SECTION_WORDS}
        assert too_long == {}, path.name


def test_answerable_questions_have_evidence_and_key_facts(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        '{"id": "x", "category": "factual", "expected_action": "answer", "split": "dev", '
        '"question": "q?"}\n',
        encoding="utf-8",
    )

    with pytest.raises(DatasetError, match="need evidence and key facts"):
        load_dataset(bad)


def test_the_expected_action_must_match_the_category(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        '{"id": "x", "category": "conflicting", "expected_action": "answer", "split": "dev", '
        '"question": "q?"}\n',
        encoding="utf-8",
    )

    with pytest.raises(DatasetError, match="expect 'escalate'"):
        load_dataset(bad)
