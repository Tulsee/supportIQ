"""The evaluation runner end to end, with the test stand-ins for the models."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.embeddings.embedder import HashingEmbedder
from ai_engine.evaluation.dataset import load_dataset
from ai_engine.evaluation.metrics import summarize
from ai_engine.evidence.policy import Thresholds
from ai_engine.generation.llm import Prompt
from ai_engine.generation.prompt import NOT_FOUND_ANSWER
from ai_engine.reranking.cross_encoder import OverlapReranker
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.core.storage import LocalObjectStorage
from app.evaluation.report import build_report, markdown, write_report
from app.evaluation.runner import EvaluationRunner, RunOptions
from app.evaluation.tuning import tune
from tests.factories import API, TEST_INGESTION_LIMITS, ScriptedLLM, create_user, in_org

pytestmark = pytest.mark.anyio

DOCUMENTS = {
    "guide.md": "# Router guide\n\n## Lights\n\nA solid amber light means there is no internet "
    "connection.\n\n## Reset\n\nHold the reset button for 10 seconds.\n",
    "faq.md": "# Old FAQ\n\n## Returns\n\nYou can return a product within 60 days.\n",
    "policy.md": "# Returns policy\n\n## Returns\n\nYou can return a product within 30 days.\n",
}
QUESTIONS = [
    {
        "id": "a1",
        "category": "factual",
        "expected_action": "answer",
        "split": "dev",
        "question": "What does a solid amber light mean?",
        "evidence": [
            {
                "document": "guide.md",
                "quote": "A solid amber light means there is no internet connection.",
            }
        ],
        "key_facts": [["no internet"]],
    },
    {
        "id": "c1",
        "category": "conflicting",
        "expected_action": "escalate",
        "split": "test",
        "question": "How many days do I have to return a product?",
        "evidence": [
            {"document": "policy.md", "quote": "You can return a product within 30 days."},
            {"document": "faq.md", "quote": "You can return a product within 60 days."},
        ],
    },
    {
        "id": "u1",
        "category": "unanswerable",
        "expected_action": "abstain",
        "split": "dev",
        "question": "What is the capital of Australia?",
    },
]


def model_reply(prompt: Prompt) -> str:
    """Answers questions about lights and returns from the passage that covers them,
    citing it; declines anything else, like a model following the rules."""
    question = prompt.user.rsplit("Question:", 1)[1]
    for topic, word, answer in (
        ("light", "amber", "It means there is no internet connection [{n}]."),
        ("return", "30 days", "You have 30 days [{n}]."),
    ):
        if topic not in question:
            continue
        for block in prompt.user.split("<passage ")[1:]:
            if word in block:
                return answer.format(n=block.split('"')[1])
    return NOT_FOUND_ANSWER


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    folder = tmp_path / "corpus"
    folder.mkdir()
    for name, text in DOCUMENTS.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


@pytest.fixture
def dataset_file(tmp_path: Path) -> Path:
    path = tmp_path / "questions.jsonl"
    path.write_text("".join(json.dumps(q) + "\n" for q in QUESTIONS), encoding="utf-8")
    return path


@pytest.fixture
def runner(
    db_session: AsyncSession,
    storage: LocalObjectStorage,
    embedder: HashingEmbedder,
    vector_store: QdrantVectorStore,
    reranker: OverlapReranker,
    llm: ScriptedLLM,
) -> EvaluationRunner:
    llm.reply = model_reply
    return EvaluationRunner(
        db_session,
        storage=storage,
        embedder=embedder,
        vector_store=vector_store,
        reranker=reranker,
        llm=llm,
        limits=TEST_INGESTION_LIMITS,
    )


async def test_runs_questions_and_scores_them(
    runner: EvaluationRunner,
    corpus: Path,
    dataset_file: Path,
    tmp_path: Path,
    api: httpx2.AsyncClient,
) -> None:
    viewer = await create_user(api)
    dataset = load_dataset(dataset_file)
    started = datetime.now(UTC)

    workspace = await runner.prepare(corpus, share_with=viewer.email)
    outcomes = await runner.run(workspace, dataset.questions, RunOptions(top_k=3))

    assert sorted(d.title for d in workspace.documents) == [
        "Old FAQ",
        "Returns policy",
        "Router guide",
    ]
    assert all(d.indexed_now for d in workspace.documents)
    answered, conflicting, unanswerable = outcomes
    assert answered.status == "answered" and answered.correct and answered.evidence_rank == 1
    assert answered.relevant_citations == 1
    assert conflicting.action == "answer" and conflicting.unsupported  # should have escalated
    assert unanswerable.action == "abstain"

    summary = summarize(outcomes, k=3)
    assert summary["answers"]["coverage"]["value"] == 1.0
    assert summary["answers"]["unsupported_answers"]["successes"] == 1
    assert summary["answers"]["abstention_recall"]["value"] == 1.0

    report = build_report(
        name="smoke test",
        dataset=dataset,
        questions=dataset.questions,
        selection={"split": "all"},
        outcomes=outcomes,
        options=RunOptions(top_k=3),
        workspace=workspace,
        corpus=corpus,
        llm=runner.llm,
        started_at=started,
        finished_at=datetime.now(UTC),
    )
    json_path, md_path = write_report(report, tmp_path / "results")
    assert json.loads(json_path.read_text(encoding="utf-8"))["metrics"]["overall"]["questions"] == 3
    text = md_path.read_text(encoding="utf-8")
    assert "## Headline" in text and "## By category" in text
    assert "`c1` (conflicting)" in text and "answer (supported); expected escalate" in text
    assert "`a1`" not in text.split("## To review")[1]
    assert report["pipeline"]["llm"]["model_versions"] == ["sha256:test"]
    assert markdown(report) == text

    # Every question is an ordinary run the shared user can open in the AI inspector.
    listed = await api.get(f"{API}/ai/runs", headers=in_org(viewer, str(workspace.organization.id)))
    assert listed.status_code == 200
    assert {run["id"] for run in listed.json()["items"]} == {o.run_id for o in outcomes}


async def test_unchanged_documents_are_reused_and_changes_reindexed(
    runner: EvaluationRunner, corpus: Path
) -> None:
    first = await runner.prepare(corpus)

    again = await runner.prepare(corpus)
    (corpus / "faq.md").write_text(
        "# Old FAQ\n\n## Returns\n\nReturns: 45 days.\n", encoding="utf-8"
    )
    (corpus / "guide.md").unlink()
    changed = await runner.prepare(corpus)

    assert again.organization.id == first.organization.id
    assert not any(d.indexed_now for d in again.documents)
    assert {d.file: d.indexed_now for d in changed.documents} == {
        "faq.md": True,
        "policy.md": False,
    }
    retrieval_only = await runner.run(
        changed, load_dataset_from(QUESTIONS[:1], corpus.parent), RunOptions(retrieval_only=True)
    )
    assert retrieval_only[0].status is None
    assert retrieval_only[0].evidence_rank is None  # its document was removed


def load_dataset_from(rows: list[dict], folder: Path) -> list:
    path = folder / "subset.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return load_dataset(path).questions


async def test_an_observe_run_can_be_tuned(
    runner: EvaluationRunner, corpus: Path, dataset_file: Path, tmp_path: Path
) -> None:
    dataset = load_dataset(dataset_file)
    workspace = await runner.prepare(corpus)
    started = datetime.now(UTC)
    options = RunOptions(top_k=3, policy="observe")

    outcomes = await runner.run(workspace, dataset.questions, options)

    assert all(o.signals is not None and o.signals["generated"] for o in outcomes)
    assert all(o.decision is not None for o in outcomes)
    report = build_report(
        name="observe",
        dataset=dataset,
        questions=dataset.questions,
        selection={"split": "all"},
        outcomes=outcomes,
        options=options,
        workspace=workspace,
        corpus=corpus,
        llm=runner.llm,
        started_at=started,
        finished_at=datetime.now(UTC),
    )
    result = tune(report, split="all")

    assert result["questions"] == 3
    assert 0 <= result["best"]["accuracy"] <= 1
    assert result["best"]["accuracy"] >= result["defaults"]["accuracy"]
    assert set(result["best"]["thresholds"]) == set(Thresholds().as_dict())
    assert report["pipeline"]["policy"] == "observe"
    with pytest.raises(ValueError, match="observe run"):
        tune({**report, "pipeline": {**report["pipeline"], "policy": "assessed"}})
