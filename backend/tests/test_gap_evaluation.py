"""The knowledge-gap evaluation (G1-G3): the dataset is well formed, the scoring is
right, and a whole run works end to end on a scripted model (one answerable topic with
a ticket that answers it, and one decoy)."""

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.contradictions import judge
from ai_engine.evaluation.metrics import normalize
from ai_engine.gaps import draft as drafting
from ai_engine.generation.llm import Prompt
from app.core.config import REPO_ROOT
from app.evaluation import gaps as evaluation
from app.evaluation.runner import EvaluationRunner
from tests.factories import TEST_INGESTION_LIMITS, ScriptedLLM
from tests.test_assessment import passage_number, reply

FOLDER = REPO_ROOT / "datasets" / "knowledge_gaps"
TOPICS = evaluation.load_topics(FOLDER / "topics_v1.jsonl")
TICKETS = evaluation.load_tickets(FOLDER / "tickets_v1.jsonl")


def test_the_set_is_well_formed() -> None:
    assert len(TOPICS) == 12 and len(TICKETS) == 23
    assert sum(not t.answerable for t in TOPICS) == 2
    assert {t.split for t in TOPICS} == {"dev", "test"}
    replies = {t.id: t.reply for t in TICKETS}
    for topic in TOPICS:
        assert len(set(topic.asked)) == len(topic.asked) >= 2
        assert topic.answerable == bool(topic.sources)
        text = normalize(" ".join(replies[s] for s in topic.sources))
        for facts in [*topic.draft_facts, *[f for h in topic.held_out for f in h.key_facts]]:
            assert any(normalize(p) in text for p in facts), (topic.id, facts)
        # Held-out questions are new phrasings, not copies of the asked ones.
        assert not {h.question for h in topic.held_out} & set(topic.asked)
    # The documents don't contain the answers the tickets give.
    corpus = normalize(
        " ".join(
            p.read_text(encoding="utf-8")
            for p in (REPO_ROOT / "datasets" / "sample_documents" / "quillon").glob("*.md")
        )
    )
    for needle in ("status.quillon.example", "5400", "network mode", "remove device", "add rule"):
        assert needle not in corpus
    # The planted outdated claim is in a reply and in the old FAQ, not in the current policy.
    assert "60 days" in normalize(replies["t05"]) and "60 days" in corpus


def test_scoring_helpers() -> None:
    assert evaluation.exact_mcnemar(0, 8) == pytest.approx(0.0078125)
    assert evaluation.exact_mcnemar(3, 3) == 1.0
    scores = evaluation.pairwise(["a", "a", "b", "b"], [0, 0, 1, 2])
    assert scores["pairs_precision"] == 1.0 and scores["pairs_recall"] == 0.5
    assert scores["clusters"] == 3 and scores["topics"] == 2
    rows = [{"threshold": 0.6, "pairs_f1": 0.8}, {"threshold": 0.7, "pairs_f1": 0.8}]
    assert evaluation.choose_threshold(rows) == 0.7  # ties: the more conservative one
    assert evaluation.facts_found("Tap Add rule; up to 20.", [["add rule"], ["30"]]) == [
        True,
        False,
    ]


# --- A whole run, scripted -------------------------------------------------------------------

STEPS = (
    "In the Quillon Home app, go to Settings, then Advanced, then Port forwarding, and tap "
    "Add rule."
)
MINI_TOPICS = [
    evaluation.Topic(
        id="g01",
        split="dev",
        answerable=True,
        topic="port forwarding",
        asked=["Set up port forwarding rules", "Port forwarding rules setup"],
        held_out=[evaluation.HeldOut("Forward a port on my Q3: how?", [["Add rule"]])],
        sources=["t01"],
        draft_facts=[["Add rule"]],
        outdated=[],
    ),
    evaluation.Topic(
        id="g02",
        split="test",
        answerable=False,
        topic="IPv6",
        asked=["IPv6 support", "Is IPv6 supported"],
        held_out=[evaluation.HeldOut("Is IPv6 available on the Q3?", [])],
        sources=[],
        draft_facts=[],
        outdated=[],
    ),
]
MINI_TICKETS = [
    evaluation.TicketRow(
        "t01",
        "g01",
        "Port forwarding for a game server",
        "Daniel Okafor",
        "daniel@example.net",
        "How do I forward a port on my Q3 router?",
        f"Hi Daniel, {STEPS} The Q3 supports up to 20 port forwarding rules.",
    ),
    evaluation.TicketRow(
        "t02",
        "g02",
        "IPv6 on the Q3?",
        "Elena Garcia",
        "elena@example.com",
        "Does the Q3 support IPv6?",
        "I've asked our engineering team about IPv6 and will get back to you.",
    ),
]


def mini_model(prompt: Prompt) -> str:
    if prompt.system == drafting.SYSTEM:
        if "IPv6" in prompt.user:
            return json.dumps({"verdict": "not_answered", "title": "", "statements": []})
        number = int(prompt.user.split('<ticket number="')[1].split('"')[0])
        statements = [
            {"text": STEPS, "ticket": number, "quote": STEPS},
            {"text": "Daniel can add 20 rules.", "ticket": number, "quote": "up to 20 port"},
        ]
        return json.dumps(
            {"verdict": "answered", "title": "Port forwarding", "statements": statements}
        )
    if prompt.system == judge.SYSTEM:
        return json.dumps({"verdict": "consistent", "confidence": "high", "conflicts": []})
    if "Add rule" in prompt.user:  # the published article was retrieved
        return reply("answered", f"Tap Add rule [{passage_number(prompt, 'Add rule')}].")
    return reply("not_found")


@pytest.mark.anyio
async def test_a_whole_run_on_a_scripted_model(
    db_session: AsyncSession,
    storage,
    embedder,
    vector_store,
    reranker,
    nli,
    llm: ScriptedLLM,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "gap_ticket_min_similarity", 0.1)  # bag-of-words embedder
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "returns.md").write_text(
        "# Returns\n\n## Returning a product\n\nUnopened routers can be returned within 30 days.\n",
        encoding="utf-8",
    )
    llm.reply = mini_model
    cached = evaluation.CachingLLM(llm, tmp_path / "cache.jsonl")
    runner = EvaluationRunner(
        db_session,
        storage=storage,
        embedder=embedder,
        vector_store=vector_store,
        reranker=reranker,
        llm=cached,  # type: ignore[arg-type]
        limits=TEST_INGESTION_LIMITS,
    )
    workspace = await runner.prepare(corpus, slug="eval-gaps-test")
    run = evaluation.GapRun(
        workspace=workspace,
        session=db_session,
        llm=cached,  # type: ignore[arg-type]
        embedder=embedder,
        vectors=vector_store,
        reranker=reranker,
        storage=storage,
        limits=TEST_INGESTION_LIMITS,
        nli=nli,
        progress=lambda _: None,
    )

    report = await evaluation.run_experiment(run, MINI_TOPICS, MINI_TICKETS, cluster_similarity=0.5)

    assert report["g0"] == {
        "asked": 4,
        "unanswered": 4,
        "by_status": {"abstained": 4},
        "answered_questions": [],
    }
    assert report["g1"]["used"] == 0.5 and report["g1"]["live"]["pairs_f1"] == 1.0
    g2 = report["g2"]["summary"]
    assert g2["answerable_drafted"]["successes"] == 1
    assert g2["decoys_drafted"]["successes"] == 0
    assert g2["source_recall"]["value"] == 1.0
    assert g2["key_facts_in_body"]["value"] == 1.0
    assert g2["left_out_by"] == {"private": 1}
    assert g2["private_details_in_bodies"] == 0
    g3 = report["g3"]
    assert g3["correct_before"]["successes"] == 0 and g3["correct_after"]["successes"] == 1
    assert g3["cite_article_after"]["successes"] == 1
    assert g3["decoys_answered_after"] == 0
    assert g3["recheck_answered"]["value"] == 1.0
    assert "## G3" in evaluation.markdown({**report, "name": "test"})

    # A rerun replays the cache: no new model calls, the same results.
    calls = cached.misses
    again = await evaluation.run_experiment(run, MINI_TOPICS, MINI_TICKETS, cluster_similarity=0.5)
    assert cached.misses == calls and cached.hits > 0
    assert again["g3"]["correct_after"] == g3["correct_after"]
