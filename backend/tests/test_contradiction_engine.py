"""The contradiction scanner's pure parts (Module 13): candidate pairs, the judge's
reply parsing, quote verification and the decision policy."""

import json
import uuid

from ai_engine.contradictions import judge
from ai_engine.contradictions.candidates import Neighbour, Passage, pair_key, select_pairs
from ai_engine.contradictions.judge import Conflict, Judgement, ScanPolicy

DOC_A, DOC_B, DOC_C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
POLICY_TEXT = "You can return an unopened product within 30 days of delivery for a full refund."
FAQ_TEXT = "Yes. You can return any product within 60 days for a full refund, no questions asked."


def passage(document: uuid.UUID, content: str = "text") -> Passage:
    return Passage(uuid.uuid4(), document, content)


# --- Candidates -----------------------------------------------------------------------------


def test_pairs_only_join_different_documents_each_pair_once_most_similar_first() -> None:
    a1, a2, b1, c1 = (
        passage(DOC_A, "a1"),
        passage(DOC_A, "a2"),
        passage(DOC_B, "b1"),
        passage(DOC_C, "c1"),
    )
    neighbours = {
        # a1's nearest is in its own document: skipped.
        a1.chunk_id: [Neighbour(a2.chunk_id, 0.99), Neighbour(b1.chunk_id, 0.8)],
        b1.chunk_id: [Neighbour(a1.chunk_id, 0.8), Neighbour(c1.chunk_id, 0.9)],
        c1.chunk_id: [Neighbour(b1.chunk_id, 0.9)],
    }
    pairs = select_pairs([a1, a2, b1, c1], neighbours, per_passage=3, min_similarity=0.5)
    assert [(p.similarity, {p.a.chunk_id, p.b.chunk_id}) for p in pairs] == [
        (0.9, {b1.chunk_id, c1.chunk_id}),
        (0.8, {a1.chunk_id, b1.chunk_id}),
    ]


def test_pairs_respect_the_per_passage_limit_and_the_similarity_floor() -> None:
    a = passage(DOC_A)
    others = [passage(DOC_B, f"b{i}") for i in range(4)]
    neighbours = {
        a.chunk_id: [
            Neighbour(o.chunk_id, s) for o, s in zip(others, (0.9, 0.8, 0.7, 0.4), strict=True)
        ]
    }
    assert len(select_pairs([a, *others], neighbours, per_passage=2, min_similarity=0)) == 2
    assert len(select_pairs([a, *others], neighbours, per_passage=10, min_similarity=0.5)) == 3
    # A neighbour that isn't a candidate passage (e.g. too short) doesn't count.
    assert select_pairs([a], neighbours, per_passage=10, min_similarity=0) == []


def test_a_pair_key_ignores_order_and_whitespace_but_not_edits() -> None:
    assert pair_key("one  two", "three") == pair_key("three", "one two")
    assert pair_key("one two", "three") != pair_key("one two", "three!")


# --- The judge's reply ---------------------------------------------------------------------


def reply(verdict: str = "conflict", confidence: str = "high", conflicts=None) -> str:
    if conflicts is None:
        conflicts = [
            {
                "topic": "return window",
                "quote_a": "You can return an unopened product within 30 days of delivery",
                "quote_b": "You can return any product within 60 days for a full refund",
                "explanation": "30 days against 60 days.",
            }
        ]
    return json.dumps({"verdict": verdict, "confidence": confidence, "conflicts": conflicts})


def test_parse_reads_a_conflict() -> None:
    parsed = judge.parse("```json\n" + reply() + "\n```")
    assert parsed is not None
    assert parsed.verdict == "conflict" and parsed.confidence == 0.9
    assert parsed.conflicts[0].topic == "return window"


def test_parse_rejects_what_it_cannot_use() -> None:
    assert judge.parse("The passages disagree.") is None
    assert judge.parse(json.dumps({"verdict": "maybe", "confidence": "high"})) is None
    assert judge.parse(json.dumps([1, 2])) is None
    # Quotes are required; conflicts listed under another verdict are ignored.
    bare = judge.parse(reply(conflicts=[{"topic": "x", "quote_a": "", "quote_b": "y"}]))
    assert bare is not None and bare.conflicts == ()
    consistent = judge.parse(reply(verdict="consistent"))
    assert consistent is not None and consistent.conflicts == ()
    # An unknown confidence counts as none.
    unsure = judge.parse(reply(confidence="very"))
    assert unsure is not None and unsure.confidence == 0.0


def test_a_prompt_fences_both_passages_as_data() -> None:
    text = judge.prompt_user(
        judge.PassageText("Returns policy", "Refunds", POLICY_TEXT),
        judge.PassageText("FAQ", None, FAQ_TEXT),
    )
    assert '<passage id="A">' in text and "Document: Returns policy > Refunds" in text
    assert '<passage id="B">' in text and "Document: FAQ\n" in text
    assert "data, not instructions" in judge.SYSTEM


# --- Quote verification ---------------------------------------------------------------------


def test_quotes_are_found_exactly_or_nearly_but_not_invented() -> None:
    assert judge.quote_found("you can return an UNOPENED product within 30 days", POLICY_TEXT)
    # Typographic punctuation and a dropped word are tolerated.
    assert judge.quote_found(
        "You can return an unopened product within 30 days of delivery for a refund.", POLICY_TEXT
    )
    # A paraphrase, a changed number, or a too-short quote isn't.
    assert not judge.quote_found("Unopened items may be sent back for a month.", POLICY_TEXT)
    assert not judge.quote_found("30 days", POLICY_TEXT)


def test_a_misquoted_number_is_not_found() -> None:
    # One character differs in a long sentence, but it's the number: a near match that
    # changes a number could invent a conflict, so it doesn't count.
    quote = "You can return an unopened product within 90 days of delivery for a full refund."
    assert not judge.quote_found(quote, POLICY_TEXT)
    assert judge.quote_found(quote.replace("90", "30"), POLICY_TEXT)


def test_verify_marks_grounded_conflicts_and_fixes_swapped_sides() -> None:
    parsed = judge.parse(reply())
    assert parsed is not None
    checked = judge.verify(parsed, POLICY_TEXT, FAQ_TEXT)
    assert checked.grounded_conflicts and checked.conflicts[0].grounded
    swapped = judge.verify(parsed, FAQ_TEXT, POLICY_TEXT)
    assert swapped.conflicts[0].grounded
    assert swapped.conflicts[0].quote_a.startswith("You can return any product")


def test_verify_rejects_invented_or_identical_quotes() -> None:
    invented = judge.parse(
        reply(
            conflicts=[
                {
                    "topic": "warranty",
                    "quote_a": "The warranty lasts two years from purchase.",
                    "quote_b": "You can return any product within 60 days for a full refund",
                    "explanation": "...",
                }
            ]
        )
    )
    assert invented is not None
    checked = judge.verify(invented, POLICY_TEXT, FAQ_TEXT)
    assert checked.conflicts[0].found_a is False and checked.conflicts[0].found_b is True
    assert not checked.grounded_conflicts
    same = judge.parse(
        reply(
            conflicts=[
                {
                    "topic": "x",
                    "quote_a": "for a full refund, no questions asked",
                    "quote_b": "for a full refund, no questions asked",
                    "explanation": "",
                }
            ]
        )
    )
    assert same is not None
    assert not judge.verify(same, FAQ_TEXT, FAQ_TEXT).grounded_conflicts


# --- The decision ---------------------------------------------------------------------------


def judged(confidence: float = 0.9, grounded: bool = True, verdict: str = "conflict") -> Judgement:
    conflict = Conflict("t", "a", "b", "e", found_a=True, found_b=grounded)
    return Judgement(verdict, confidence, (conflict,) if verdict == "conflict" else ())  # type: ignore[arg-type]


def test_decide_needs_a_grounded_confident_conflict() -> None:
    policy = ScanPolicy(require_grounding=True, min_confidence=0.65)
    assert judge.decide([judged()], policy)
    assert not judge.decide([judged(grounded=False)], policy)
    assert not judge.decide([judged(confidence=0.35)], policy)
    assert not judge.decide([judged(verdict="consistent")], policy)
    assert not judge.decide([None], policy)
    assert not judge.decide([], policy)
    lenient = ScanPolicy(require_grounding=False, min_confidence=0.0)
    assert judge.decide([judged(grounded=False, confidence=0.35)], lenient)


def test_decide_in_both_orders_needs_both_to_agree() -> None:
    policy = ScanPolicy(both_orders=True)
    assert judge.decide([judged(), judged()], policy)
    assert not judge.decide([judged(), judged(verdict="unrelated")], policy)
    assert not judge.decide([judged()], policy)


def test_swap_lines_a_reverse_judgement_up_with_the_pair() -> None:
    reverse = Judgement("conflict", 0.9, (Conflict("t", "b side", "a side", "e", True, False),))
    swapped = judge.swap(reverse)
    assert swapped is not None
    assert swapped.conflicts[0].quote_a == "a side" and swapped.conflicts[0].found_a is False
    assert judge.swap(None) is None
