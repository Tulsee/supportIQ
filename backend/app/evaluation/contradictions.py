"""Evaluating the contradiction scanner (Module 13, experiments C1-C3) on the Quillon
knowledge base and its six planted contradictions.

    C1  candidate pairs: how many of the planted contradictions are among the pairs
        sent to the model, and how many pairs that costs, across neighbour settings
    C2  the judge on the default candidates, under decision policies of increasing
        strictness (the same model replies, re-scored):
            J0  the model's verdict alone
            J1  + confidence at least medium
            J2  + both quotes found in their passages (the default policy)
            J3  J2, and the reverse order (B, A) must agree too
            J4  quotes found, any confidence (isolates the grounding check)
    C3  cost: model calls and time per scan, against comparing every pair

The gold labels are the evidence of the dataset's ``conflicting`` questions: each names
two documents and the conflicting sentence in each. A candidate pair is a planted
pair when its passages contain both quotes. Every other pair counts as non-conflicting
for the strict precision; flagged pairs outside the planted set are listed for a
person to check (the documents could contain conflicts nobody planted).

The model judges every candidate pair in both orders, once; replies are cached (JSON
lines, keyed by model, pair and order) so the run can resume and the policies are
compared on identical replies.
"""

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anyio import to_thread

from ai_engine.contradictions import judge
from ai_engine.contradictions.judge import Judgement, ScanPolicy
from ai_engine.evaluation.dataset import Dataset
from ai_engine.evaluation.metrics import contains, rate
from ai_engine.generation.llm import LLMError
from app.services.contradictions import Candidate, ContradictionScanner

POLICIES: dict[str, tuple[str, ScanPolicy]] = {
    "J0": ("Model verdict only", ScanPolicy(require_grounding=False, min_confidence=0.0)),
    "J1": (
        "+ confidence at least medium",
        ScanPolicy(require_grounding=False, min_confidence=0.65),
    ),
    "J2": ("+ quotes found in both passages (default)", ScanPolicy(True, 0.65)),
    "J3": ("J2, both orders agree", ScanPolicy(True, 0.65, both_orders=True)),
    "J4": ("Quotes found, any confidence", ScanPolicy(True, 0.0)),
}
DEFAULT_POLICY = "J2"
NEIGHBOUR_GRID = (1, 2, 3, 5, 10)
SIMILARITY_GRID = (0.0, 0.5, 0.6, 0.7, 0.8)


@dataclass(frozen=True)
class Planted:
    id: str  # the question it comes from
    split: str
    topic: str  # the question, as a label
    sides: tuple[tuple[str, str], tuple[str, str]]  # (file, quote) for each document


def planted_conflicts(dataset: Dataset) -> list[Planted]:
    found = []
    for q in dataset.questions:
        if q.category != "conflicting":
            continue
        if len(q.evidence) != 2:
            raise ValueError(f"{q.id}: a conflict needs exactly two quotes")
        (a, b) = q.evidence
        found.append(
            Planted(q.id, q.split, q.question, ((a.document, a.quote), (b.document, b.quote)))
        )
    return found


def covers(candidate: Candidate, files: dict[Any, str], planted: Planted) -> bool:
    """Whether the pair's passages hold both sides of ``planted`` (either order)."""
    (file_a, quote_a), (file_b, quote_b) = planted.sides
    sides = [
        (files.get(candidate.a.document_id), candidate.a.content),
        (files.get(candidate.b.document_id), candidate.b.content),
    ]
    for (fa, ta), (fb, tb) in (sides, sides[::-1]):
        if fa == file_a and fb == file_b and contains(ta, quote_a) and contains(tb, quote_b):
            return True
    return False


def labels(
    candidates: Sequence[Candidate], files: dict[Any, str], planted: Sequence[Planted]
) -> dict[str, list[str]]:
    """Pair key -> the planted contradictions it holds (usually none)."""
    return {c.pair.key: [p.id for p in planted if covers(c, files, p)] for c in candidates}


# --- C1 -------------------------------------------------------------------------------------


async def candidate_grid(
    scanner_for: Callable[[int, float], ContradictionScanner],
    organization_id: Any,
    files: dict[Any, str],
    planted: Sequence[Planted],
) -> list[dict[str, Any]]:
    rows = []
    for k in NEIGHBOUR_GRID:
        for threshold in SIMILARITY_GRID:
            candidates = await scanner_for(k, threshold).candidates(organization_id)
            found = sorted(
                {pid for ids in labels(candidates, files, planted).values() for pid in ids}
            )
            rows.append(
                {
                    "neighbours": k,
                    "min_similarity": threshold,
                    "pairs": len(candidates),
                    "planted_found": found,
                    "recall": rate(len(found), len(planted)),
                }
            )
    return rows


def planted_similarity(
    candidates: Sequence[Candidate], files: dict[Any, str], planted: Sequence[Planted]
) -> dict[str, float | None]:
    """Each planted pair's similarity (from a wide candidate set), to show the margin."""
    out: dict[str, float | None] = {}
    for p in planted:
        hits = [c.pair.similarity for c in candidates if covers(c, files, p)]
        out[p.id] = round(max(hits), 4) if hits else None
    return out


# --- Model replies --------------------------------------------------------------------------


def read_cache(cache: Path) -> dict[str, dict[str, Any]]:
    done: dict[str, dict[str, Any]] = {}
    if cache.exists():
        for line in cache.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                done[entry["key"]] = entry
    return done


async def collect_replies(
    scanner: ContradictionScanner,
    candidates: Sequence[Candidate],
    cache: Path,
    progress: Callable[[int, int, Candidate, str, dict[str, Any]], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """The model's reply to every pair in both orders ("ab", "ba"), cached."""
    done = await to_thread.run_sync(read_cache, cache)
    cache.parent.mkdir(parents=True, exist_ok=True)
    total = len(candidates) * 2
    number = 0
    with cache.open("a", encoding="utf-8") as out:
        for candidate in candidates:
            for order in ("ab", "ba"):
                number += 1
                key = f"{candidate.pair.key}:{order}"
                if key in done:
                    continue
                first, second = (
                    (candidate.a, candidate.b) if order == "ab" else (candidate.b, candidate.a)
                )
                started = time.perf_counter()
                entry: dict[str, Any] = {"key": key, "raw": None, "error": None}
                try:
                    _, entry["raw"] = await scanner.ask(first, second)
                except LLMError as exc:
                    entry["error"] = str(exc)
                entry["latency_ms"] = round((time.perf_counter() - started) * 1000)
                done[key] = entry
                out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                out.flush()
                if progress:
                    progress(number, total, candidate, order, entry)
    return done


def judgements(candidate: Candidate, replies: dict[str, dict[str, Any]]) -> list[Judgement | None]:
    """Both orders, parsed and verified now (so a change to verification is re-scored
    without asking the model again), the reverse aligned to (A, B)."""
    out: list[Judgement | None] = []
    for order in ("ab", "ba"):
        entry = replies.get(f"{candidate.pair.key}:{order}") or {}
        parsed = judge.parse(entry["raw"]) if entry.get("raw") else None
        if parsed is None:
            out.append(None)
            continue
        if order == "ab":
            out.append(judge.verify(parsed, candidate.a.content, candidate.b.content))
        else:
            out.append(judge.swap(judge.verify(parsed, candidate.b.content, candidate.a.content)))
    return out


# --- C2 -------------------------------------------------------------------------------------


def localized(judgement: Judgement | None, planted: Planted) -> bool:
    """Whether a grounded conflict quotes the planted sentences (either way round)."""
    if judgement is None:
        return False
    (_, quote_a), (_, quote_b) = planted.sides

    def same(model_quote: str, gold: str) -> bool:
        return contains(model_quote, gold) or contains(gold, model_quote)

    for c in judgement.grounded_conflicts:
        if (same(c.quote_a, quote_a) and same(c.quote_b, quote_b)) or (
            same(c.quote_a, quote_b) and same(c.quote_b, quote_a)
        ):
            return True
    return False


def score_policy(
    policy: ScanPolicy,
    candidates: Sequence[Candidate],
    replies: dict[str, dict[str, Any]],
    gold: dict[str, list[str]],
    planted: Sequence[Planted],
) -> dict[str, Any]:
    flagged_pairs = []
    found: set[str] = set()
    located: set[str] = set()
    by_id = {p.id: p for p in planted}
    for c in candidates:
        both = judgements(c, replies)
        if not judge.decide(both, policy):
            continue
        flagged_pairs.append(c.pair.key)
        for pid in gold.get(c.pair.key, []):
            found.add(pid)
            if localized(both[0], by_id[pid]):
                located.add(pid)
    true_pairs = [k for k in flagged_pairs if gold.get(k)]
    precision = rate(len(true_pairs), len(flagged_pairs))
    recall = rate(len(found), len(planted))
    p, r = precision["value"] or 0.0, recall["value"] or 0.0
    return {
        "flagged": len(flagged_pairs),
        "true_pairs": len(true_pairs),
        "false_pairs": len(flagged_pairs) - len(true_pairs),
        "precision": precision,
        "recall": recall,
        "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
        "planted_found": sorted(found),
        "planted_localized": sorted(located),
        "flagged_keys": flagged_pairs,
    }


def reply_stats(
    candidates: Sequence[Candidate], replies: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    entries = [
        replies.get(f"{c.pair.key}:{order}") or {} for c in candidates for order in ("ab", "ba")
    ]
    latencies = sorted(e["latency_ms"] for e in entries if e.get("latency_ms") is not None)
    unusable = sum(1 for e in entries if e.get("raw") and judge.parse(e["raw"]) is None)
    verdicts: dict[str, int] = {}
    for c in candidates:
        first = judgements(c, replies)[0]
        name = first.verdict if first else "no reply"
        verdicts[name] = verdicts.get(name, 0) + 1
    order_agreement = sum(
        1
        for c in candidates
        if (pair := judgements(c, replies))[0] is not None
        and pair[1] is not None
        and (pair[0].verdict == "conflict") == (pair[1].verdict == "conflict")
    )
    return {
        "calls": len(entries),
        "errors": sum(1 for e in entries if e.get("error")),
        "unusable_replies": unusable,
        "verdicts_ab": verdicts,
        "order_agreement": rate(order_agreement, len(candidates)),
        "latency_ms": {
            "mean": round(sum(latencies) / len(latencies)) if latencies else None,
            "p50": latencies[len(latencies) // 2] if latencies else None,
            "max": latencies[-1] if latencies else None,
        },
    }


def flagged_details(
    keys: Sequence[str],
    candidates: Sequence[Candidate],
    replies: dict[str, dict[str, Any]],
    gold: dict[str, list[str]],
) -> list[dict[str, Any]]:
    by_key = {c.pair.key: c for c in candidates}
    out = []
    for key in keys:
        c = by_key[key]
        first = judgements(c, replies)[0]
        out.append(
            {
                "pair_key": key,
                "planted": gold.get(key, []),
                "similarity": round(c.pair.similarity, 4),
                "a": {"document": c.a.document_title, "section": c.a.section},
                "b": {"document": c.b.document_title, "section": c.b.section},
                "confidence": first.confidence if first else None,
                "conflicts": [x.to_json() for x in first.conflicts] if first else [],
            }
        )
    return out


# --- Report ---------------------------------------------------------------------------------


def _pct(r: dict[str, Any]) -> str:
    if r["value"] is None:
        return "-"
    low, high = r["ci95"]
    return f"{r['value']:.0%} ({r['successes']}/{r['n']}; {low:.0%}-{high:.0%})"


def markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Contradiction scanner: {report['name']}",
        "",
        f"Model `{report['model']}`, embeddings `{report['embedding_model']}`, "
        f"{report['passages']} passages in {report['documents']} documents "
        f"({report['all_pairs']} cross-document pairs), {len(report['planted'])} planted "
        f"contradictions. Default candidates: {report['defaults']['neighbours']} neighbours, "
        f"similarity >= {report['defaults']['min_similarity']}.",
        "",
        "## C1: candidate pairs",
        "",
        "| Neighbours | Min similarity | Pairs | Share of all pairs | Planted found |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["c1"]["grid"]:
        share = row["pairs"] / report["all_pairs"] if report["all_pairs"] else 0
        lines.append(
            f"| {row['neighbours']} | {row['min_similarity']} | {row['pairs']} | {share:.0%} | "
            f"{_pct(row['recall'])} |"
        )
    lines += [
        "",
        "Similarity of each planted pair: "
        + ", ".join(f"{k} {v}" for k, v in report["c1"]["planted_similarity"].items()),
        "",
        "## C2: the judge on the default candidates",
        "",
        f"{report['c2']['candidates']} pairs ({report['c2']['planted_pairs']} hold a planted "
        "contradiction).",
        "",
        "| Policy | Flagged | Precision (planted) | Recall | F1 | Quotes match planted |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, row in report["c2"]["policies"].items():
        lines.append(
            f"| {name}: {row['label']} | {row['flagged']} | {_pct(row['precision'])} | "
            f"{_pct(row['recall'])} | {row['f1']:.2f} | "
            f"{len(row['planted_localized'])}/{len(row['planted_found'])} |"
        )
    stats = report["c2"]["replies"]
    lines += [
        "",
        f"Model calls {stats['calls']}, errors {stats['errors']}, unusable replies "
        f"{stats['unusable_replies']}; verdicts (A, B): {stats['verdicts_ab']}; the two "
        f"orders agree on conflict or not for {_pct(stats['order_agreement'])} of pairs; "
        f"latency mean {stats['latency_ms']['mean']} ms, median {stats['latency_ms']['p50']} ms.",
        "",
        "## C3: cost of a scan",
        "",
    ]
    c3 = report["c3"]
    lines += [
        f"- A first scan with the default policy asks the model {c3['calls_default']} times "
        f"(about {c3['minutes_default']} minutes at the measured mean latency); comparing "
        f"every cross-document pair would take {c3['calls_all_pairs']} calls (about "
        f"{c3['minutes_all_pairs']} minutes).",
        "- Later scans ask only about new or changed passages.",
        "",
        "## Flagged pairs (default policy)",
        "",
    ]
    for item in report["c2"]["flagged_default"]:
        label = ", ".join(item["planted"]) or "NOT PLANTED (check by hand)"
        lines.append(
            f"- **{label}** ({item['similarity']}): {item['a']['document']} > "
            f"{item['a']['section']} / {item['b']['document']} > {item['b']['section']}"
        )
        for c in item["conflicts"]:
            mark = "" if c["found_a"] and c["found_b"] else " (quote not found)"
            lines.append(f'  - {c["topic"]}{mark}: "{c["quote_a"]}" vs "{c["quote_b"]}"')
    return "\n".join(lines) + "\n"
