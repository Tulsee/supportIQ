"""Evaluating distortion-aware faithfulness checks (research feature F6) on D5.

Every condition flags a pair as distorted or not:

- ``rules``: any deterministic rule objects (``ai_engine.faithfulness.rules``); each rule
  is also reported on its own.
- ``nli-contradiction``: the NLI model finds the quote contradicts the statement with
  probability at or above a threshold (what the gap drafter does today, at 0.5).
- ``nli-entailment``: the NLI model doesn't find the quote entails the statement
  (entailment below a threshold): requiring entailment.
- ``judge``: a language model's verdict (one call per pair, cached).
- combinations: a pair is flagged if any member flags it.

The NLI thresholds are chosen on the dev split: the most distortions caught with at
most ``max_false`` of the faithful statements flagged (hypothesis H8: 5%); ties go to
the stricter threshold. Everything is reported on the test split, with Wilson
intervals, per distortion type, and for the hard items.
"""

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_engine.evaluation.metrics import rate
from ai_engine.evidence.nli import NliModel
from ai_engine.faithfulness import judge as judging
from ai_engine.faithfulness import rules
from ai_engine.generation.llm import LLMClient, LLMError, Prompt

TYPES = ("role", "scope", "polarity", "modality", "quantity")
GRID = tuple(round(0.05 * i, 2) for i in range(1, 20))
# Fixed before the judge's run. "@0.5" is the gap drafter's setting today; the plain
# "nli-contradiction" uses the threshold chosen on dev.
COMBINATIONS: dict[str, tuple[str, ...]] = {
    "rules + nli@0.5": ("rules", "nli-contradiction@0.5"),
    "rules + nli-contradiction": ("rules", "nli-contradiction"),
    "rules + judge": ("rules", "judge"),
    "nli@0.5 + judge": ("nli-contradiction@0.5", "judge"),
    "rules + nli@0.5 + judge": ("rules", "nli-contradiction@0.5", "judge"),
}


@dataclass(frozen=True)
class Pair:
    id: str
    source: str
    split: str
    quote: str
    context: str
    statement: str
    label: str  # "faithful" or "distorted"
    type: str  # "none" or a distortion type
    hard: bool
    note: str

    @property
    def distorted(self) -> bool:
        return self.label == "distorted"


def load_pairs(path: Path) -> list[Pair]:
    return [
        Pair(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@dataclass
class Scored:
    pair: Pair
    findings: list[rules.Finding]
    entailment: float
    contradiction: float
    judgement: judging.Judgement | None = None
    judge_error: str | None = None
    judge_ms: float | None = None
    flags: dict[str, bool] = field(default_factory=dict)


# --- Scoring ------------------------------------------------------------------------------


def score_offline(pairs: Sequence[Pair], nli: NliModel) -> list[Scored]:
    """Rules and NLI for every pair (no language model)."""
    scores = nli.score([(p.quote, p.statement) for p in pairs])
    return [
        Scored(p, rules.check(p.statement, p.quote, p.context), s.entailment, s.contradiction)
        for p, s in zip(pairs, scores, strict=True)
    ]


async def judge_all(
    scored: Sequence[Scored],
    llm: LLMClient,
    progress: Callable[[int, int, Scored], None] | None = None,
) -> None:
    """Ask the judge about every pair (the client caches replies, so a rerun replays)."""
    for number, item in enumerate(scored, start=1):
        p = item.pair
        started = time.perf_counter()
        try:
            completion = await llm.complete(
                Prompt(
                    judging.SYSTEM,
                    judging.prompt_user(p.statement, p.quote, p.context),
                    judging.SCHEMA,
                )
            )
        except LLMError as exc:
            item.judge_error = str(exc)
        else:
            item.judgement = judging.parse(completion.text)
            if item.judgement is None:
                item.judge_error = "unusable reply"
            item.judge_ms = (completion.timings_ms or {}).get("total") or round(
                (time.perf_counter() - started) * 1000, 1
            )
        if progress:
            progress(number, len(scored), item)


def choose_threshold(
    dev: Sequence[Scored], value: Callable[[Scored], float], above: bool, max_false: float
) -> float | None:
    """The threshold that catches the most dev distortions while flagging at most
    ``max_false`` of the dev faithful statements; ties go to the stricter threshold.
    ``above``: flag when the value is at or above the threshold (else below it)."""
    faithful = [s for s in dev if not s.pair.distorted]
    distorted = [s for s in dev if s.pair.distorted]
    best: tuple[int, float] | None = None
    for threshold in GRID:

        def flagged(s: Scored, t: float = threshold) -> bool:
            return value(s) >= t if above else value(s) < t

        false = sum(flagged(s) for s in faithful) / max(len(faithful), 1)
        if false > max_false:
            continue
        caught = sum(flagged(s) for s in distorted)
        # Stricter: a higher threshold when flagging above it, a lower one below it.
        key = (caught, threshold if above else -threshold)
        if best is None or key > (best[0], best[1] if above else -best[1]):
            best = (caught, threshold)
    return best[1] if best else None


def flag_all(scored: Sequence[Scored], thresholds: dict[str, float | None], judged: bool) -> None:
    for s in scored:
        flags = {
            "rules": bool(s.findings),
            **{f"rule:{r}": any(f.rule == r for f in s.findings) for r in rules.RULES},
            "nli-contradiction@0.5": s.contradiction >= 0.5,
        }
        if thresholds.get("nli-contradiction") is not None:
            flags["nli-contradiction"] = s.contradiction >= thresholds["nli-contradiction"]  # type: ignore[operator]
        if thresholds.get("nli-entailment") is not None:
            flags["nli-entailment"] = s.entailment < thresholds["nli-entailment"]  # type: ignore[operator]
        if judged:
            flags["judge"] = bool(s.judgement and s.judgement.distorted)
        for name, members in COMBINATIONS.items():
            if all(m in flags for m in members):
                flags[name] = any(flags[m] for m in members)
        s.flags = flags


def metrics(scored: Sequence[Scored], condition: str) -> dict[str, Any]:
    faithful = [s for s in scored if not s.pair.distorted]
    distorted = [s for s in scored if s.pair.distorted]
    caught = [s for s in distorted if s.flags.get(condition)]
    return {
        "recall": rate(len(caught), len(distorted)),
        "false_rejection": rate(sum(bool(s.flags.get(condition)) for s in faithful), len(faithful)),
        "by_type": {
            t: rate(
                sum(bool(s.flags.get(condition)) for s in distorted if s.pair.type == t),
                sum(1 for s in distorted if s.pair.type == t),
            )
            for t in TYPES
        },
        "hard_recall": rate(
            sum(bool(s.flags.get(condition)) for s in distorted if s.pair.hard),
            sum(1 for s in distorted if s.pair.hard),
        ),
        "hard_false_rejection": rate(
            sum(bool(s.flags.get(condition)) for s in faithful if s.pair.hard),
            sum(1 for s in faithful if s.pair.hard),
        ),
    }


def evaluate(scored: Sequence[Scored], *, judged: bool, max_false: float) -> dict[str, Any]:
    dev = [s for s in scored if s.pair.split == "dev"]
    test = [s for s in scored if s.pair.split == "test"]
    thresholds = {
        "nli-contradiction": choose_threshold(dev, lambda s: s.contradiction, True, max_false),
        "nli-entailment": choose_threshold(dev, lambda s: s.entailment, False, max_false),
    }
    flag_all(scored, thresholds, judged)
    conditions = [c for c in scored[0].flags] if scored else []
    return {
        "thresholds": thresholds,
        "max_false": max_false,
        "conditions": {c: {"dev": metrics(dev, c), "test": metrics(test, c)} for c in conditions},
        "pairs": [
            {
                "id": s.pair.id,
                "split": s.pair.split,
                "label": s.pair.label,
                "type": s.pair.type,
                "hard": s.pair.hard,
                "statement": s.pair.statement,
                "quote": s.pair.quote,
                "rules": [{"rule": f.rule, "detail": f.detail} for f in s.findings],
                "entailment": round(s.entailment, 4),
                "contradiction": round(s.contradiction, 4),
                "judge": (
                    {
                        "verdict": s.judgement.verdict,
                        "type": s.judgement.kind,
                        "explanation": s.judgement.explanation,
                    }
                    if s.judgement
                    else None
                ),
                "judge_error": s.judge_error,
                "judge_ms": s.judge_ms,
                "flags": s.flags,
            }
            for s in scored
        ],
    }


# --- Report --------------------------------------------------------------------------------


def _pct(r: dict[str, Any]) -> str:
    if not r["n"]:
        return "—"
    low, high = r["ci95"]
    return f"{r['successes']}/{r['n']} ({r['value']:.0%}, {low:.0%}-{high:.0%})"


def _short(r: dict[str, Any]) -> str:
    return f"{r['successes']}/{r['n']}" if r["n"] else "—"


def markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Faithfulness checks (F6): {report.get('name', 'D5')}",
        "",
        f"Dataset: {report['dataset']} ({report['counts']}) · NLI: {report['nli_model']} · "
        f"judge: {report.get('judge_model') or 'not run'} · cache hits "
        f"{report.get('cache', {}).get('hits')}, model calls "
        f"{report.get('cache', {}).get('misses')}",
        "",
        f"Thresholds chosen on dev (at most {report['max_false']:.0%} of faithful statements "
        f"flagged): {report['thresholds']}",
        "",
        "## Test split",
        "",
        "| Condition | Distortions caught | Faithful flagged | "
        + " | ".join(TYPES)
        + " | Hard caught | Hard faithful flagged |",
        "| --- | --- | --- | " + " | ".join("---" for _ in TYPES) + " | --- | --- |",
    ]
    for name, result in report["conditions"].items():
        m = result["test"]
        lines.append(
            f"| {name} | {_pct(m['recall'])} | {_pct(m['false_rejection'])} | "
            + " | ".join(_short(m["by_type"][t]) for t in TYPES)
            + f" | {_short(m['hard_recall'])} | {_short(m['hard_false_rejection'])} |"
        )
    lines += ["", "## Dev split", "", "| Condition | Distortions caught | Faithful flagged |",
              "| --- | --- | --- |"]  # fmt: skip
    for name, result in report["conditions"].items():
        m = result["dev"]
        lines.append(f"| {name} | {_pct(m['recall'])} | {_pct(m['false_rejection'])} |")
    lines += [
        "",
        "## Test pairs: faithful statements flagged, distortions missed (by rules + NLI at 0.5)",
        "",
    ]
    for p in report["pairs"]:
        if p["split"] != "test":
            continue
        flag = p["flags"].get("rules + nli@0.5")
        if (p["label"] == "faithful") == bool(flag):
            what = "flagged" if flag else f"missed ({p['type']})"
            rule_text = "; ".join(f"{r['rule']}: {r['detail']}" for r in p["rules"]) or "no rule"
            judge = p["judge"]["verdict"] if p["judge"] else "—"
            lines.append(
                f"- **{p['id']}** {what}: {p['statement']}  \n"
                f"  quote: {p['quote']}  \n"
                f"  rules: {rule_text}; contradiction {p['contradiction']:.2f}, entailment "
                f"{p['entailment']:.2f}; judge: {judge}"
            )
    return "\n".join(lines) + "\n"


def write(report: dict[str, Any], output: Path, name: str) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output / f"{stamp}-{name}.json"
    md_path = output / f"{stamp}-{name}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), "utf-8")
    md_path.write_text(markdown(report), "utf-8")
    return json_path, md_path
