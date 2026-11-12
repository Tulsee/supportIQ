"""Compare evaluation runs side by side (the plan's experiments E1-E6).

Runs are only comparable on the same dataset: the table says so when hashes differ.
"""

import json
from pathlib import Path
from typing import Any

ROWS: list[tuple[str, str, str]] = [
    # (label, section, key); a key containing "@" uses the run's top-k
    ("Questions", "", "questions"),
    ("Evidence hit@k", "retrieval", "hit@k"),
    ("Evidence recall@k", "retrieval", "recall@k"),
    ("MRR@10", "retrieval", "mrr@10"),
    ("nDCG@10", "retrieval", "ndcg@10"),
    ("Coverage (answerable answered)", "answers", "coverage"),
    ("Correctness (key facts)", "answers", "correctness"),
    ("Unsupported answers", "answers", "unsupported_answers"),
    ("Unwarranted answers", "answers", "unwarranted_answers"),
    ("Abstention precision", "answers", "abstention_precision"),
    ("Abstention recall", "answers", "abstention_recall"),
    ("Decision accuracy", "answers", "decision_accuracy"),
    ("Citation precision", "answers", "citation_precision"),
    ("Evidence score Brier (lower is better)", "calibration", "brier"),
    ("Evidence score ECE (lower is better)", "calibration", "ece"),
    ("Latency p50", "latency", "p50_ms"),
    ("Latency p95", "latency", "p95_ms"),
]


def _value(metrics: dict[str, Any], section: str, key: str, k: int) -> str:
    source = metrics.get(section, {}) if section else metrics
    if source is None:
        return "-"
    value = source.get(key.replace("@k", f"@{k}"))
    if value is None:
        return "-"
    if isinstance(value, dict):  # a rate with an interval
        if value.get("value") is None:
            return "-"
        low, high = value["ci95"]
        return f"{value['value']:.0%} ({value['successes']}/{value['n']}; {low:.0%}-{high:.0%})"
    if key.endswith("_ms"):
        return f"{value:.0f} ms" if value < 1000 else f"{value / 1000:.1f} s"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def compare(paths: list[Path], split: str = "all") -> str:
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    names = [r["name"] for r in reports]
    lines = [f"# Comparison ({'all questions' if split == 'all' else f'{split} split'})", ""]
    for r in reports:
        p = r["pipeline"]
        llm = p.get("llm") or {}
        lines.append(
            f"- **{r['name']}**: {p['mode']}, strategy {p['strategy']}, top {p['top_k']}, "
            f"{'reranked' if p['rerank'] else 'not reranked'}"
            + (f", policy {p.get('policy')}" if p.get("policy") else "")
            + (f", {llm.get('model')} ({llm.get('prompt_version')})" if llm else "")
            + f"; dataset {r['dataset']['sha256'][:12]}, started {r['started_at']}"
        )
    if len({r["dataset"]["sha256"] for r in reports}) > 1:
        lines += ["", "**Warning: the runs used different datasets; don't compare them.**"]
    header = "| Metric | " + " | ".join(names) + " |"
    lines += ["", header, "| --- |" + " --- |" * len(names)]
    for label, section, key in ROWS:
        cells = []
        for r in reports:
            metrics = (
                r["metrics"]["overall"]
                if split == "all"
                else r["metrics"]["by_split"].get(split, {})
            )
            cells.append(_value(metrics, section, key, r["pipeline"]["top_k"]))
        if any(c != "-" for c in cells):
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
