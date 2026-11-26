"""Faithfulness checks (research feature F6): the D5 pairs, the rules, the judge's reply
parsing, and the evaluation's threshold choice. No database, no model."""

import json
from collections import defaultdict

import pytest

from ai_engine.faithfulness import judge, rules
from app.core.config import REPO_ROOT
from app.evaluation import faithfulness as evaluation

PAIRS = REPO_ROOT / "datasets" / "distortions" / "pairs_v1.jsonl"


def flagged(statement: str, quote: str, context: str = "") -> set[str]:
    return {f.rule for f in rules.check(statement, quote, context)}


# --- The dataset (D5) ------------------------------------------------------------------------


def test_every_pair_is_well_formed_and_sources_stay_in_one_split() -> None:
    pairs = evaluation.load_pairs(PAIRS)
    assert len(pairs) == len({p.id for p in pairs}) >= 200
    splits: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, set[str]] = defaultdict(set)
    for p in pairs:
        assert p.quote in p.context, p.id
        assert p.label in ("faithful", "distorted") and p.split in ("dev", "test")
        assert (p.type == "none") == (p.label == "faithful"), p.id
        assert p.type in ("none", *evaluation.TYPES)
        assert p.statement != p.quote
        splits[p.source + p.quote].add(p.split)
        labels[p.source + p.quote].add(p.label)
    assert all(len(s) == 1 for s in splits.values())  # no source in both splits
    assert all(ls == {"faithful", "distorted"} for ls in labels.values())
    for kind in evaluation.TYPES:
        for split in ("dev", "test"):
            assert sum(1 for p in pairs if p.type == kind and p.split == split) >= 8


def test_the_builder_reproduces_the_file() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_pairs_v1", REPO_ROOT / "datasets" / "distortions" / "build_pairs_v1.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    built = module.build()
    on_disk = [json.loads(line) for line in PAIRS.read_text("utf-8").splitlines() if line.strip()]
    assert built == on_disk


# --- Rules -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "quote"),
    [
        # Changed number, number written as a word, changed unit, swapped numbers.
        ("Up to 25 rules are supported.", "The Q3 supports up to 20 port forwarding rules."),
        (
            "Wait ten minutes, then restart the router.",
            "Wait two minutes, then restart the router.",
        ),
        ("The reset link is valid for 30 hours.", "The reset link is valid for 30 minutes."),
        (
            "Returns are accepted within 15 days, minus a 14% fee.",
            "Opened products can be returned within 14 days of delivery, minus a 15% "
            "restocking fee.",
        ),
        (
            "It covers up to 5 cameras and VPN for up to 4 devices.",
            "It adds 30 days of history for up to 4 cameras, plus VPN protection for up to 5 "
            "devices.",
        ),
        ("Secure Plus costs $9.99 per year.", "Secure Plus costs $9.99 per month."),
    ],
)
def test_quantity_changes_are_found(statement: str, quote: str) -> None:
    assert "quantity" in flagged(statement, quote)


@pytest.mark.parametrize(
    ("statement", "quote"),
    [
        # The same numbers, measured the same way, however worded.
        (
            "A 128 GB card stores roughly 12 days of continuous 1080p video.",
            "With continuous 1080p recording, a 128 GB card holds about 12 days of video",
        ),
        (
            "Up to 20 port forwarding rules can be set on the Q3.",
            "The Q3 supports up to 20 port forwarding rules.",
        ),
        (
            "You can delay one for up to 7 days in the app.",  # "one" is a pronoun here
            "You can postpone an update for up to 7 days in the app.",
        ),
        (
            "After three failures in a row, contact support.",
            "If it fails three times in a row, contact support.",
        ),
        (
            "New firmware is installed between 2 and 4 a.m.",
            "Routers install new firmware between 2 a.m. and 4 a.m.",
        ),
    ],
)
def test_reworded_quantities_pass(statement: str, quote: str) -> None:
    assert "quantity" not in flagged(statement, quote)


def test_names_must_come_from_the_source_and_we_stands_for_the_organization() -> None:
    quote = "The Q3 supports up to 20 port forwarding rules."
    assert "name" in flagged("The QC2 supports up to 20 port forwarding rules.", quote)
    assert "name" not in flagged(
        "The Q3 works with Amazon Alexa and Google Home.",
        "It works with Amazon Alexa and Google Home for voice commands.",
        context="HomeKit support?\nCan I control my Q3 from the Apple Home app?",
    )
    assert "name" in flagged(
        "Phone support is available until 9 p.m. Pacific Time.",
        "Phone support is available until 9 p.m. Eastern Time.",
    )
    assert not flagged(
        "Quillon does not currently offer student discounts.",
        "We don't offer student, teacher or military discounts at the moment.",
    )


def test_negation_is_compared_with_the_part_of_the_quote_restated() -> None:
    quote = (
        "In bridge mode your satellite nodes keep working, but parental controls, the guest "
        "network and Quillon Secure malware blocking are not available, because your other "
        "router manages the network."
    )
    assert "polarity" not in flagged("Satellite nodes keep working in bridge mode.", quote)
    assert "polarity" in flagged(
        "In bridge mode, parental controls and the guest network remain available.", quote
    )
    merged = "No restart is needed: new port forwarding rules take effect within about a minute."
    assert "polarity" not in flagged(
        "New port forwarding rules start working within about a minute, without restarting.", merged
    )
    assert "polarity" in flagged("A restart is needed before new rules take effect.", merged)
    assert rules.negations("The Q3 allows no more than 20 rules.") == 0
    assert rules.negations("can" + chr(0x2019) + "t join") == 1


def test_modality_changes_are_found_and_negated_obligations_are_not_obligations() -> None:
    open_question = (
        "I've asked our engineering team about IPv6 on the Q3 and I'll get back to you as soon "
        "as I hear from them."
    )
    assert "modality" in flagged("The Q3 does not support IPv6.", open_question)
    assert "modality" not in flagged(
        "The engineering team has been asked about IPv6 on the Q3.", open_question
    )
    assert "modality" in flagged(
        "The Q3 will support Apple HomeKit soon.", "The Q3 doesn't support Apple HomeKit."
    )
    can = "You can change the delivery address yourself within 2 hours of placing the order"
    assert "modality" in flagged("You must change the delivery address within 2 hours.", can)
    assert "modality" not in flagged(
        "You don't have to restart the Q3 after adding a rule.",
        "No restart is needed: new rules take effect within about a minute.",
    )


def test_roles_the_source_never_names_are_found() -> None:
    quote = (
        "Before you sell or give away a Q3, open the Quillon Home app, go to Devices, select "
        "the router and tap Remove device, then do a factory reset."
    )
    assert "role" in flagged("Before selling, the buyer should tap Remove device.", quote)
    assert "role" not in flagged(
        "A 128 GB card stores roughly 12 days of video.",  # "stores" is a verb here
        "a 128 GB card holds about 12 days of video",
    )


# --- The judge's reply --------------------------------------------------------------------------


def test_the_judges_reply_is_parsed() -> None:
    reply = {"verdict": "distorted", "type": "role", "explanation": "The buyer is not the seller."}
    parsed = judge.parse(json.dumps(reply))
    assert parsed is not None and parsed.distorted and parsed.kind == "role"
    fenced = judge.parse("```json\n" + json.dumps({**reply, "type": "weird"}) + "\n```")
    assert fenced is not None and fenced.kind == "other"
    assert judge.parse("It looks fine to me.") is None
    assert judge.parse(json.dumps({"verdict": "maybe", "type": "none", "explanation": ""})) is None
    user = judge.prompt_user("S", "Q", "C" * 2000)
    assert "<statement>\nS\n</statement>" in user and user.count("C") < 1600


# --- The evaluation ------------------------------------------------------------------------------


def scored(label: str, contradiction: float, split: str = "dev", kind: str = "none"):
    pair = evaluation.Pair(
        id=f"x{contradiction}{label}",
        source="s",
        split=split,
        quote="q",
        context="",
        statement="s",
        label=label,
        type=kind if label == "distorted" else "none",
        hard=False,
        note="",
    )
    return evaluation.Scored(pair, [], 1 - contradiction, contradiction)


def test_the_threshold_catches_most_while_flagging_few_faithful_statements() -> None:
    dev = [
        scored("faithful", 0.02),
        scored("faithful", 0.30),
        scored("distorted", 0.40, kind="scope"),
        scored("distorted", 0.90, kind="polarity"),
    ]
    # 0.3 or below would flag a faithful statement (50% > 5%); 0.35 and 0.4 both catch
    # both distortions, and a tie goes to the stricter threshold.
    assert evaluation.choose_threshold(dev, lambda s: s.contradiction, True, 0.05) == 0.4
    evaluation.flag_all(dev, {"nli-contradiction": 0.4}, judged=False)
    result = evaluation.metrics(dev, "nli-contradiction")
    assert result["recall"]["successes"] == 2 and result["false_rejection"]["successes"] == 0
    assert result["by_type"]["polarity"]["successes"] == 1
    assert dev[0].flags["rules + nli-contradiction"] is False
