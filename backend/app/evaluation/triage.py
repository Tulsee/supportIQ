"""Evaluating AI triage (Module 11, experiments T1-T4) on the labelled triage set.

    T1  the language model alone (it abstains only by answering "unsure")
    T2  the neighbour vote alone (labelled examples: the dev split)
    T3  both combined, always applying the top category
    T4  both combined with abstention (alpha and threshold tuned on dev)
    T5  T4, but the neighbour vote only for languages the embedding model reads
        (added after the T4 error analysis: tuned on dev, reported separately)

The model is called once per ticket and its replies are cached (the run can resume),
so T1-T4 and any re-tuning reuse the same replies. Neighbours for a test ticket are
the dev tickets; for a dev ticket, the other dev tickets (leave one out). Parameters
are chosen on dev and every number that matters is reported on test.
"""

import json
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from anyio import to_thread

from ai_engine.embeddings.embedder import Embedder
from ai_engine.evaluation.metrics import calibration, rate, wilson
from ai_engine.generation.llm import LLMClient, LLMError, Prompt
from ai_engine.triage import llm as triage_llm
from ai_engine.triage.combine import TriagePolicy, combine
from ai_engine.triage.llm import CategoryOption, LlmVote
from ai_engine.triage.neighbours import NeighbourVote, vote

CONFIGS = {
    "T1": "Language model only",
    "T2": "Neighbours only",
    "T3": "Combined, always apply",
    "T4": "Combined with abstention",
    "T5": "T4, neighbours only in the embedder's languages (post hoc)",
}
# The languages the embedding model reads (bge-small-en: English).
EMBEDDER_LANGUAGES = ("en",)
ALPHAS = tuple(round(0.1 * i, 1) for i in range(1, 10))  # 0.1 ... 0.9
THRESHOLDS = tuple(round(0.3 + 0.025 * i, 3) for i in range(27))  # 0.30 ... 0.95


@dataclass(frozen=True)
class TriageItem:
    id: str
    split: str
    category: str
    priority: str
    language: str
    ambiguous: bool
    subject: str
    body: str


@dataclass(frozen=True)
class Prediction:
    id: str
    gold: str
    predicted: str | None  # the top guess
    confidence: float
    applied: bool
    ambiguous: bool

    @property
    def correct(self) -> bool:
        return self.applied and self.predicted == self.gold


def load_items(path: Path) -> list[TriageItem]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            items.append(TriageItem(**{k: row[k] for k in TriageItem.__dataclass_fields__}))
    return items


def load_categories(path: Path) -> list[CategoryOption]:
    return [CategoryOption(**c) for c in json.loads(path.read_text(encoding="utf-8"))]


# --- Signals -------------------------------------------------------------------------------


def read_cache(cache: Path) -> dict[str, dict[str, Any]]:
    done: dict[str, dict[str, Any]] = {}
    if cache.exists():
        for line in cache.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                done[entry["id"]] = entry
    return done


async def collect_votes(
    items: Sequence[TriageItem],
    categories: Sequence[CategoryOption],
    llm: LLMClient,
    cache: Path,
    progress: Callable[[int, int, TriageItem, dict[str, Any]], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """The model's reply for every ticket, cached in ``cache`` (JSON lines) so a run
    can stop and resume. Each entry: vote (or None), raw reply, error, latency."""
    done = await to_thread.run_sync(read_cache, cache)
    keys = [c.key for c in categories]
    schema = triage_llm.schema(categories)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("a", encoding="utf-8") as out:
        for number, item in enumerate(items, 1):
            if item.id in done:
                continue
            started = time.perf_counter()
            entry: dict[str, Any] = {"id": item.id, "vote": None, "raw": None, "error": None}
            try:
                completion = await llm.complete(
                    Prompt(
                        system=triage_llm.SYSTEM,
                        user=triage_llm.prompt_user(categories, item.subject, item.body),
                        json_schema=schema,
                    )
                )
                entry["raw"] = completion.text
                entry["model"] = completion.model
                parsed = triage_llm.parse(completion.text, keys)
                entry["vote"] = parsed.to_json() if parsed else None
                if parsed is None:
                    entry["error"] = "unparsable reply"
            except LLMError as exc:
                entry["error"] = str(exc)
            entry["latency_ms"] = round((time.perf_counter() - started) * 1000)
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")
            out.flush()
            done[item.id] = entry
            if progress:
                progress(number, len(items), item, entry)
    return done


def as_vote(entry: dict[str, Any] | None) -> LlmVote | None:
    if not entry or not entry.get("vote"):
        return None
    v = entry["vote"]
    return LlmVote(
        v["category"], v["confidence"], v["priority"], v["language"], tuple(v["urgency_cues"])
    )


def embed_items(items: Sequence[TriageItem], embedder: Embedder) -> dict[str, list[float]]:
    texts = [f"{i.subject.strip()}\n\n{i.body.strip()}" for i in items]
    return dict(zip([i.id for i in items], embedder.embed_documents(texts), strict=True))


@dataclass(frozen=True)
class NeighbourSettings:
    k: int = 10
    min_similarity: float = 0.5
    min_neighbours: int = 2


def neighbour_votes(
    items: Sequence[TriageItem], vectors: dict[str, list[float]], settings: NeighbourSettings
) -> dict[str, NeighbourVote | None]:
    pool = [i for i in items if i.split == "dev"]
    votes = {}
    for item in items:
        examples = [p for p in pool if p.id != item.id]
        votes[item.id] = vote(
            vectors[item.id],
            [vectors[p.id] for p in examples],
            [p.category for p in examples],
            k=settings.k,
            min_similarity=settings.min_similarity,
            min_neighbours=settings.min_neighbours,
        )
    return votes


# --- Configurations -----------------------------------------------------------------------


def predict(
    config: str,
    items: Sequence[TriageItem],
    llm_votes: dict[str, LlmVote | None],
    nn_votes: dict[str, NeighbourVote | None],
    policy: TriagePolicy,
) -> list[Prediction]:
    predictions = []
    for item in items:
        llm, nn = llm_votes.get(item.id), nn_votes.get(item.id)
        if config == "T5" and llm and llm.language and llm.language not in EMBEDDER_LANGUAGES:
            nn = None  # the embedder can't read it: its neighbours are noise
        if config == "T1":
            guess = llm.category if llm else None
            confidence, applied = (llm.confidence if llm else 0.0), guess is not None
        elif config == "T2":
            guess = nn.top if nn else None
            confidence, applied = (nn.confidence if nn else 0.0), guess is not None
        else:
            decision = combine(llm, nn, policy)
            guess, confidence = decision.category, decision.confidence
            applied = guess is not None if config == "T3" else decision.confident
        predictions.append(
            Prediction(item.id, item.category, guess, confidence, applied, item.ambiguous)
        )
    return predictions


def macro_f1(predictions: Sequence[Prediction], labels: Sequence[str]) -> float | None:
    """Over applied predictions: per category F1, averaged over the categories that
    occur (as the true or the applied category) among the evaluated tickets."""
    applied = [p for p in predictions if p.applied]
    if not applied:
        return None
    present = {p.gold for p in predictions} | {p.predicted for p in applied}
    scores = []
    for label in (label for label in labels if label in present):
        tp = sum(1 for p in applied if p.predicted == label and p.gold == label)
        fp = sum(1 for p in applied if p.predicted == label and p.gold != label)
        fn = sum(1 for p in applied if p.predicted != label and p.gold == label)
        scores.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return round(statistics.fmean(scores), 4)


def risk_coverage(predictions: Sequence[Prediction]) -> tuple[list[dict[str, float]], float | None]:
    """Applying the most confident first: risk (error rate among applied) at each
    coverage, and the area under that curve (AURC, lower is better)."""
    ranked = sorted(
        (p for p in predictions if p.predicted is not None), key=lambda p: -p.confidence
    )
    if not ranked:
        return [], None
    points, errors = [], 0
    for n, p in enumerate(ranked, 1):
        errors += p.predicted != p.gold
        points.append({"coverage": n / len(predictions), "risk": errors / n})
    aurc = statistics.fmean(point["risk"] for point in points) * len(ranked) / len(predictions)
    return points, round(aurc, 4)


def metrics(predictions: Sequence[Prediction], labels: Sequence[str]) -> dict[str, Any]:
    n = len(predictions)
    applied = [p for p in predictions if p.applied]
    correct = sum(p.correct for p in applied)
    ambiguous = [p for p in predictions if p.ambiguous]
    points, aurc = risk_coverage(predictions)
    guessed = [p for p in predictions if p.predicted is not None]
    return {
        "n": n,
        "coverage": rate(len(applied), n),
        "accuracy_applied": rate(correct, len(applied)),
        "accuracy_all": rate(correct, n),
        "macro_f1_applied": macro_f1(predictions, labels),
        "calibration": calibration(
            [(p.confidence, p.predicted == p.gold) for p in guessed], bins=5
        ),
        "aurc": aurc,
        "risk_coverage": [points[i] for i in range(0, len(points), max(len(points) // 20, 1))],
        "ambiguous": {
            "n": len(ambiguous),
            "coverage": rate(sum(p.applied for p in ambiguous), len(ambiguous)),
            "accuracy_applied": rate(
                sum(p.correct for p in ambiguous), sum(p.applied for p in ambiguous)
            ),
        },
    }


def tune(
    dev: Sequence[TriageItem],
    llm_votes: dict[str, LlmVote | None],
    nn_votes: dict[str, NeighbourVote | None],
    target: float,
    config: str = "T4",
) -> dict[str, Any]:
    """The alpha and threshold with the highest dev coverage whose dev accuracy on applied
    predictions reaches ``target``; failing that, the highest accuracy."""
    best: tuple[tuple[float, float, float], dict[str, Any]] | None = None
    for alpha in ALPHAS:
        for threshold in THRESHOLDS:
            preds = predict(config, dev, llm_votes, nn_votes, TriagePolicy(alpha, threshold))
            applied = [p for p in preds if p.applied]
            if not applied:
                continue
            accuracy = sum(p.correct for p in applied) / len(applied)
            coverage = len(applied) / len(preds)
            meets = accuracy >= target
            key = (1.0 if meets else 0.0, coverage if meets else accuracy, accuracy)
            if best is None or key > best[0]:
                best = (
                    key,
                    {
                        "alpha": alpha,
                        "threshold": threshold,
                        "dev_accuracy": round(accuracy, 4),
                        "dev_coverage": round(coverage, 4),
                        "meets_target": meets,
                    },
                )
    if best is None:
        return {"alpha": 0.5, "threshold": 0.7, "meets_target": False}
    return {**best[1], "target_accuracy": target}


# --- The run ------------------------------------------------------------------------------


def secondary(items: Sequence[TriageItem], llm_votes: dict[str, LlmVote | None]) -> dict[str, Any]:
    """What the model suggests besides the category: priority and language."""
    order = ["low", "normal", "high", "urgent"]
    with_vote = [(i, llm_votes[i.id]) for i in items if llm_votes.get(i.id)]
    exact = sum(v.priority == i.priority for i, v in with_vote)
    near = sum(
        v.priority is not None and abs(order.index(v.priority) - order.index(i.priority)) <= 1
        for i, v in with_vote
    )
    language = sum(v.language == i.language for i, v in with_vote)
    return {
        "n": len(with_vote),
        "priority_exact": rate(exact, len(with_vote)),
        "priority_within_one": rate(near, len(with_vote)),
        "language": rate(language, len(with_vote)),
    }


def evaluate(
    items: Sequence[TriageItem],
    categories: Sequence[CategoryOption],
    replies: dict[str, dict[str, Any]],
    vectors: dict[str, list[float]],
    *,
    target: float = 0.95,
    neighbour_settings: NeighbourSettings | None = None,
    fixed: TriagePolicy | None = None,
) -> dict[str, Any]:
    neighbour_settings = neighbour_settings or NeighbourSettings()
    labels = [c.key for c in categories]
    llm_votes = {i.id: as_vote(replies.get(i.id)) for i in items}
    nn_votes = neighbour_votes(items, vectors, neighbour_settings)
    dev = [i for i in items if i.split == "dev"]
    test = [i for i in items if i.split == "test"]
    tuning = (
        {"alpha": fixed.alpha, "threshold": fixed.threshold, "fixed": True}
        if fixed
        else tune(dev, llm_votes, nn_votes, target)
    )
    policy = TriagePolicy(tuning["alpha"], tuning["threshold"])
    tuning_t5 = tuning if fixed else tune(dev, llm_votes, nn_votes, target, config="T5")
    policies = {"T5": TriagePolicy(tuning_t5["alpha"], tuning_t5["threshold"])}
    configs = {}
    for config, name in CONFIGS.items():
        chosen = policies.get(config, policy)
        configs[config] = {
            "name": name,
            "dev": metrics(predict(config, dev, llm_votes, nn_votes, chosen), labels),
            "test": metrics(predict(config, test, llm_votes, nn_votes, chosen), labels),
        }
    t4_test = predict("T4", test, llm_votes, nn_votes, policy)
    per_category = {}
    for label in labels:
        gold = [p for p in t4_test if p.gold == label]
        applied = [p for p in gold if p.applied]
        per_category[label] = {
            "n": len(gold),
            "coverage": rate(len(applied), len(gold)),
            "accuracy_applied": rate(sum(p.correct for p in applied), len(applied)),
        }
    latencies = [r["latency_ms"] for r in replies.values() if r.get("latency_ms")]
    return {
        "items": {"total": len(items), "dev": len(dev), "test": len(test)},
        "tuning": tuning,
        "tuning_t5": tuning_t5,
        "neighbours": asdict(neighbour_settings),
        "configs": configs,
        "t4_per_category": per_category,
        "secondary_test": secondary(test, llm_votes),
        "model_failures": sum(1 for i in items if llm_votes[i.id] is None),
        "latency_ms": {
            "median": statistics.median(latencies) if latencies else None,
            "n": len(latencies),
        },
        "t4_test_predictions": [asdict(p) for p in t4_test],
    }


def _pct(metric: dict[str, Any] | None) -> str:
    if not metric or metric.get("value") is None:
        return "—"
    ci = metric.get("ci95")
    text = f"{metric['value'] * 100:.1f}%"
    return f"{text} ({ci[0] * 100:.0f}-{ci[1] * 100:.0f})" if ci else text


def markdown(report: dict[str, Any]) -> str:
    r = report["results"]
    t = r["tuning"]
    lines = [
        f"# Triage evaluation: {report['name']}",
        "",
        f"- Dataset: `{report['dataset']}` ({r['items']['total']} tickets: "
        f"{r['items']['dev']} dev, {r['items']['test']} test)",
        f"- Model: {report['model']} · embeddings: {report['embedding_model']}",
        f"- Tuned on dev: alpha {t['alpha']}, threshold {t['threshold']}"
        + (
            f" (dev accuracy {t.get('dev_accuracy')}, coverage {t.get('dev_coverage')}; "
            f"target {t.get('target_accuracy')}, {'met' if t.get('meets_target') else 'not met'})"
            if not t.get("fixed")
            else " (fixed)"
        ),
        f"- T5 (post hoc) tuned on dev: alpha {r['tuning_t5']['alpha']}, threshold "
        f"{r['tuning_t5']['threshold']}",
        f"- Neighbours: k {r['neighbours']['k']}, minimum similarity "
        f"{r['neighbours']['min_similarity']}, examples from the dev split",
        f"- Model failures: {r['model_failures']} · median model latency "
        f"{(r['latency_ms']['median'] or 0) / 1000:.1f} s",
        f"- Git: {report['git'].get('commit', '?')[:10]}"
        + (" (uncommitted changes)" if report["git"].get("dirty") else ""),
        "",
        "## Test split (95% Wilson intervals)",
        "",
        "| | Configuration | Coverage | Accuracy (applied) | Macro-F1 (applied) "
        "| Accuracy (all) | AURC | ECE | Ambiguous coverage / accuracy |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for config, data in r["configs"].items():
        m = data["test"]
        amb = m["ambiguous"]
        lines.append(
            f"| {config} | {data['name']} | {_pct(m['coverage'])} | {_pct(m['accuracy_applied'])} "
            f"| {m['macro_f1_applied'] if m['macro_f1_applied'] is not None else '—'} "
            f"| {_pct(m['accuracy_all'])} | {m['aurc'] if m['aurc'] is not None else '—'} "
            f"| {m['calibration'].get('ece') if m['calibration'].get('ece') is not None else '—'} "
            f"| {_pct(amb['coverage'])} / {_pct(amb['accuracy_applied'])} |"
        )
    lines += [
        "",
        "## Dev split",
        "",
        "| | Coverage | Accuracy (applied) | Macro-F1 (applied) |",
        "| --- | ---: | ---: | ---: |",
    ]
    for config, data in r["configs"].items():
        m = data["dev"]
        lines.append(
            f"| {config} | {_pct(m['coverage'])} | {_pct(m['accuracy_applied'])} "
            f"| {m['macro_f1_applied'] if m['macro_f1_applied'] is not None else '—'} |"
        )
    lines += [
        "",
        "## T4 by category (test)",
        "",
        "| Category | Tickets | Coverage | Accuracy (applied) |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, m in r["t4_per_category"].items():
        lines.append(
            f"| {label} | {m['n']} | {_pct(m['coverage'])} | {_pct(m['accuracy_applied'])} |"
        )
    s = r["secondary_test"]
    lines += [
        "",
        "## Suggestions besides the category (test, model replies)",
        "",
        f"- Priority exactly right: {_pct(s['priority_exact'])}; within one level: "
        f"{_pct(s['priority_within_one'])}",
        f"- Language right: {_pct(s['language'])}",
        "",
    ]
    return "\n".join(lines)


def build_report(
    name: str,
    dataset: str,
    model: str,
    embedding_model: str,
    results: dict[str, Any],
    git: dict[str, Any],
    environment: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": dataset,
        "model": model,
        "embedding_model": embedding_model,
        "git": git,
        "environment": environment,
        "results": results,
    }


__all__ = ["CONFIGS", "build_report", "evaluate", "markdown", "wilson"]
