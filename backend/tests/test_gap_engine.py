"""Knowledge gaps (Module 13): grouping questions and checking drafts, without a model."""

import json

from ai_engine.gaps import cluster, draft
from ai_engine.gaps.draft import Draft, Statement, TicketSource

# --- Grouping ---------------------------------------------------------------------------


def test_query_keys_ignore_case_spacing_and_trailing_punctuation() -> None:
    assert cluster.query_key("  How do I set up PORT forwarding?? ") == (
        "how do i set up port forwarding"
    )
    assert cluster.query_key("Port   forwarding.") == cluster.query_key("port forwarding")


def test_average_linkage_groups_similar_vectors_and_keeps_others_apart() -> None:
    vectors = [
        [1.0, 0.0, 0.0],
        [0.95, 0.05, 0.0],
        [0.0, 1.0, 0.0],
        [0.9, 0.1, 0.0],
        [0.0, 0.0, 1.0],
    ]
    assert cluster.cluster(vectors, threshold=0.9) == [[0, 1, 3], [2], [4]]
    assert cluster.cluster(vectors, threshold=1.01) == [[0], [1], [2], [3], [4]]
    assert cluster.cluster([], threshold=0.5) == []


def test_average_linkage_does_not_chain_loosely_related_questions() -> None:
    # a~b and b~c are similar, a and c are not: single linkage would join all three.
    a, b, c = [1.0, 0.0], [0.8, 0.6], [0.28, 0.96]
    assert cluster.cosine(a, b) >= 0.8 and cluster.cosine(b, c) >= 0.8
    assert cluster.cosine(a, c) < 0.5
    groups = cluster.cluster([a, b, c], threshold=0.75)
    assert len(groups) == 2


def test_new_questions_join_the_closest_gap_above_the_threshold() -> None:
    centroids = [cluster.centroid([[1.0, 0.0], [0.9, 0.1]]), [0.0, 1.0]]
    assert cluster.assign([[0.95, 0.05], [0.1, 0.9], [0.7, 0.7]], centroids, 0.8) == [
        0,
        1,
        None,
    ]


def test_the_representative_is_the_most_asked_then_the_medoid() -> None:
    vectors = [[1.0, 0.0], [0.9, 0.1], [0.5, 0.5]]
    assert cluster.representative([0, 1, 2], [1, 5, 2], vectors) == 1
    # A tie on counts: the one closest to the others.
    assert cluster.representative([0, 1, 2], [3, 3, 3], vectors) == 1


# --- Drafts -----------------------------------------------------------------------------

TICKETS = [
    TicketSource(
        number=12,
        subject="Port forwarding on my Q3",
        customer_text="Hi, I'm Priya. How do I forward port 25565 for my game server?",
        replies=(
            "Hi Priya, thanks for waiting. In the Quillon Home app, go to Settings, then "
            "Advanced, then Port forwarding, and tap Add rule. You can add up to 20 rules. "
            "Your order QN-48213 is not affected."
        ),
    ),
    TicketSource(
        number=15,
        subject="Gaming ports",
        customer_text="Do I need to restart after adding a rule?",
        replies="No restart is needed: new port forwarding rules apply within a minute.",
    ),
]
PRIVATE = draft.private_terms(["Priya Sharma", "Abe Agent"], ["priya.sharma@example.com"])


def reply(statements: list[dict], verdict: str = "answered", title: str = "Port forwarding") -> str:
    return json.dumps({"verdict": verdict, "title": title, "statements": statements})


def test_verified_statements_make_the_body_and_failures_are_kept_but_left_out() -> None:
    parsed = draft.parse(
        reply(
            [
                {
                    "text": "Go to Settings, then Advanced, then Port forwarding, and tap "
                    "Add rule.",
                    "ticket": 12,
                    "quote": "go to Settings, then Advanced, then Port forwarding, and tap "
                    "Add rule",
                },
                # The wrong ticket number: corrected, not penalized.
                {
                    "text": "New rules apply within a minute, without a restart.",
                    "ticket": 12,
                    "quote": "new port forwarding rules apply within a minute",
                },
                # A changed number.
                {
                    "text": "You can add up to 50 rules.",
                    "ticket": 12,
                    "quote": "You can add up to 20 rules.",
                },
                # An invented quote.
                {
                    "text": "Port forwarding needs a Secure Plus plan.",
                    "ticket": 15,
                    "quote": "Port forwarding requires Secure Plus.",
                },
                # The customer's name and order number.
                {
                    "text": "Priya, your order QN-48213 is not affected.",
                    "ticket": 12,
                    "quote": "Your order QN-48213 is not affected.",
                },
            ]
        )
    )
    assert parsed is not None
    checked = draft.verify(parsed, TICKETS, PRIVATE)
    first, second, changed, invented, private = checked.statements
    assert first.included and first.problems == []
    assert second.included and second.ticket == 15
    assert changed.problems == ["number_not_in_quote"]
    assert invented.problems == ["quote_not_found"]
    assert private.problems == ["private"]
    assert {"Priya", "QN-48213"} <= set(private.private)
    assert checked.body() == (
        "Go to Settings, then Advanced, then Port forwarding, and tap Add rule. "
        "New rules apply within a minute, without a restart."
    )
    assert [s["included"] for s in checked.to_json()["statements"]] == [
        True,
        True,
        False,
        False,
        False,
    ]


def test_not_answered_has_no_statements_and_bad_json_is_none() -> None:
    parsed = draft.parse(reply([{"text": "x", "ticket": 1, "quote": "y"}], verdict="not_answered"))
    assert parsed is not None and parsed.statements == () and parsed.body() == ""
    assert draft.parse("Sure! Here is an article.") is None
    assert draft.parse(json.dumps({"verdict": "maybe", "title": "", "statements": []})) is None
    fenced = "```json\n" + reply([]) + "\n```"
    assert draft.parse(fenced) is not None


def test_private_details_are_found_without_flagging_ordinary_words() -> None:
    terms = draft.private_terms(["Will Long"], ["wlong@example.com"])
    assert draft.find_private("The router will take a long time to restart.", terms) == []
    assert draft.find_private("Thanks, Will.", terms) == ["Will"]
    assert "wlong@example.com" in draft.find_private("Write to WLONG@example.com", terms)
    assert draft.find_private("Signed, wlong", terms) == ["wlong"]  # the email's local part
    # Contact details and references of any customer.
    found = draft.find_private(
        "Call +1 (415) 555-0199 or mail a.b@x.io about ticket #4821 and order 48213.", terms
    )
    assert found == ["a.b@x.io", "+1 (415) 555-0199", "ticket #4821", "order 48213"]
    assert draft.find_private("See #4821.", terms) == ["#4821"]
    # Not private: dates, versions, prices, product codes, error codes.
    plain = "Since 2026-11-18, version 4.2.1 costs $14.99; error E-102 on the QC2 and Q3."
    assert draft.find_private(plain, terms) == []


def test_a_private_title_is_dropped() -> None:
    parsed = Draft(
        "answered", "Priya's port forwarding", (Statement("Tap Add rule.", 12, "tap Add rule"),)
    )
    assert draft.verify(parsed, TICKETS, PRIVATE).title == ""


def test_statements_a_conflict_was_quoted_from_are_left_out() -> None:
    parsed = draft.parse(
        reply(
            [
                {
                    "text": "Go to Settings, then Advanced, then Port forwarding.",
                    "ticket": 12,
                    "quote": "go to Settings, then Advanced, then Port forwarding",
                },
                {
                    "text": "You can add up to 20 rules.",
                    "ticket": 12,
                    "quote": "You can add up to 20 rules.",
                },
            ]
        )
    )
    assert parsed is not None
    checked = draft.mark_conflicts(
        draft.verify(parsed, TICKETS, PRIVATE), ["You can add up to 20 rules."]
    )
    assert [s.problems for s in checked.statements] == [[], ["conflict"]]
    assert checked.body() == "Go to Settings, then Advanced, then Port forwarding."


def test_the_prompt_fences_tickets_as_data_and_never_shows_internal_notes() -> None:
    text = draft.prompt_user(["How do I forward a port?"] * 9, TICKETS)
    assert text.count("<ticket number=") == 2 and "</questions>" in text
    assert text.count("How do I forward a port?") == draft.MAX_QUESTIONS
    assert "Agent replied:" in text and "Customer wrote:" in text


def test_the_article_is_markdown_with_the_title_as_heading() -> None:
    assert draft.article_markdown("  Port   forwarding ", "Tap Add rule.\n") == (
        "# Port forwarding\n\nTap Add rule.\n"
    )
