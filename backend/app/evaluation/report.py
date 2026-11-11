"""Evaluation reports: everything needed to reproduce and compare a run (JSON),
and a readable summary (Markdown) listing the questions worth reviewing."""

import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from ai_engine.evaluation.dataset import Dataset, Question
from ai_engine.evaluation.metrics import QuestionOutcome, by_group, summarize
from ai_engine.generation.llm import LLMClient
from ai_engine.generation.prompt import PROMPT_VERSION
from app.core.config import REPO_ROOT, settings
from app.evaluation.runner import RunOptions, Workspace

_PACKAGES = ("fastembed", "onnxruntime", "qdrant-client", "anthropic", "httpx2", "sqlalchemy")


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _git() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    status = git("status", "--porcelain")
    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def _environment() -> dict[str, Any]:
    versions = {}
    for package in _PACKAGES:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "git": _git(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpus": os.cpu_count(),
        "packages": versions,
    }


def _question_record(outcome: QuestionOutcome) -> dict[str, Any]:
    q = outcome.question
    record: dict[str, Any] = {
        "id": q.id,
        "category": q.category,
        "split": q.split,
        "question": q.question,
        "expected_action": q.expected_action,
        "run_id": outcome.run_id,
        "evidence_rank": outcome.evidence_rank if q.evidence else None,
        "latency_ms": round(outcome.latency_ms, 1),
    }
    if outcome.status is not None:
        record |= {
            "status": outcome.status,
            "action": outcome.action,
            "answer": outcome.answer,
            "missing_facts": outcome.missing_facts if q.answerable else None,
            "correct": outcome.correct if q.answerable else None,
            "unsupported": outcome.unsupported,
            "citations": len(outcome.cited_passages),
            "relevant_citations": outcome.relevant_citations,
            "invalid_citations": outcome.invalid_citations,
            "generation_ms": outcome.generation_ms,
            "error": outcome.error,
        }
    return record


def build_report(
    *,
    name: str,
    dataset: Dataset,
    questions: list[Question],
    selection: dict[str, Any],
    outcomes: list[QuestionOutcome],
    options: RunOptions,
    workspace: Workspace,
    corpus: Path,
    llm: LLMClient,
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    k = options.top_k
    answers = not options.retrieval_only
    versions = sorted({o.model_version for o in outcomes if o.model_version})
    return {
        "name": name,
        "started_at": started_at.isoformat(timespec="seconds"),
        "duration_s": round((finished_at - started_at).total_seconds(), 1),
        "dataset": {
            "name": dataset.name,
            "path": _relative(dataset.path),
            "sha256": dataset.sha256,
            "questions": len(questions),
            "selection": selection,
        },
        "corpus": {
            "path": _relative(corpus),
            "organization_id": str(workspace.organization.id),
            "documents": [
                {"file": d.file, "sha256": d.sha256, "title": d.title, "chunks": d.chunks}
                for d in workspace.documents
            ],
        },
        "pipeline": {
            "mode": "answer" if answers else "retrieval",
            "strategy": options.strategy.value,
            "rerank": options.rerank,
            "top_k": k,
            "embedding_model": settings.embedding_model,
            "reranker_model": settings.reranker_model if options.rerank else None,
            "chunking": {
                "size_tokens": settings.chunk_size_tokens,
                "overlap_tokens": settings.chunk_overlap_tokens,
            },
            "retrieval": {
                "sparse_top_k": settings.retrieval_sparse_top_k,
                "dense_top_k": settings.retrieval_dense_top_k,
                "rrf_k": settings.retrieval_rrf_k,
                "rerank_candidates": settings.retrieval_rerank_candidates,
                "bm25": {"k1": settings.bm25_k1, "b": settings.bm25_b},
            },
            "llm": {
                "provider": llm.provider,
                "model": llm.model,
                "model_versions": versions,
                "config": llm.config,
                "prompt_version": PROMPT_VERSION,
            }
            if answers
            else None,
        },
        "environment": _environment(),
        "metrics": {
            "overall": summarize(outcomes, k=k, answers=answers),
            "by_split": {
                split: summarize(group, k=k, answers=answers)
                for split, group in sorted(by_group(outcomes, lambda o: o.question.split).items())
            },
            "by_category": {
                category: summarize(group, k=k, answers=answers)
                for category, group in by_group(outcomes, lambda o: o.question.category).items()
            },
        },
        "questions": [_question_record(o) for o in outcomes],
    }


# --- Markdown ------------------------------------------------------------------------------------


def _rate(metric: dict[str, Any] | None) -> str:
    if not metric or metric.get("value") is None:
        return "n/a"
    low, high = metric["ci95"]
    return f"{metric['value']:.0%} ({metric['successes']}/{metric['n']}; {low:.0%}-{high:.0%})"


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value / 1000:.1f} s"


def _headline_rows(report: dict[str, Any]) -> list[tuple[str, str]]:
    overall = report["metrics"]["overall"]
    k = report["pipeline"]["top_k"]
    retrieval = overall["retrieval"]
    rows = []
    if "answers" in overall:
        answers = overall["answers"]
        rows += [
            ("Coverage: answerable questions answered", _rate(answers["coverage"])),
            ("Correctness: answers with every key fact", _rate(answers["correctness"])),
            ("Unsupported answers (headline)", _rate(answers["unsupported_answers"])),
            (
                "Unwarranted answers: should not have answered",
                _rate(answers["unwarranted_answers"]),
            ),
            ("Abstention precision", _rate(answers["abstention_precision"])),
            ("Abstention recall", _rate(answers["abstention_recall"])),
            ("Citation precision", _rate(answers["citation_precision"])),
            ("Answers without citations", _rate(answers["uncited_answers"])),
            ("Invented citations removed", str(answers["invalid_citations"])),
            ("Model failures", str(answers["failures"])),
        ]
    rows += [
        (f"Evidence hit@{k}", _rate(retrieval[f"hit@{k}"])),
        (
            f"Evidence recall@{k}",
            f"{retrieval[f'recall@{k}']:.2f}" if retrieval[f"recall@{k}"] is not None else "n/a",
        ),
        ("MRR@10", f"{retrieval['mrr@10']:.2f}" if retrieval["mrr@10"] is not None else "n/a"),
        (
            "Latency p50 / p95",
            f"{_ms(overall['latency']['p50_ms'])} / {_ms(overall['latency']['p95_ms'])}",
        ),
    ]
    return rows


def _review(report: dict[str, Any]) -> list[str]:
    lines = []
    for q in report["questions"]:
        if "status" not in q:
            continue
        reason = None
        if q["status"] == "failed":
            reason = f"failed: {q['error']}"
        elif q["expected_action"] != "answer" and q["action"] == "answer":
            reason = f"answered; expected {q['expected_action']}"
        elif q["expected_action"] == "answer" and q["action"] != "answer":
            reason = f"{q['status']}; expected an answer"
        elif q["missing_facts"]:
            reason = "missing " + ", ".join(f"'{f}'" for f in q["missing_facts"])
        if reason:
            lines.append(
                f"- `{q['id']}` ({q['category']}) {q['question']!r}: {reason}. Run `{q['run_id']}`"
            )
    return lines


def markdown(report: dict[str, Any]) -> str:
    pipeline = report["pipeline"]
    llm = pipeline["llm"]
    model = (
        f"{llm['model']} via {llm['provider']}"
        + (
            f" ({', '.join(v[:12] for v in llm['model_versions'])})"
            if llm["model_versions"]
            else ""
        )
        + f", prompt {llm['prompt_version']}"
        if llm
        else "retrieval only (no model)"
    )
    git = report["environment"]["git"]
    code = (git["commit"] or "unknown")[:10] + (" (uncommitted changes)" if git["dirty"] else "")
    lines = [
        f"# Evaluation: {report['name']}",
        "",
        f"- Started {report['started_at']}, took {report['duration_s']:.0f} s",
        f"- Dataset `{report['dataset']['path']}` ({report['dataset']['questions']} questions, "
        f"sha256 {report['dataset']['sha256'][:12]}), selection {report['dataset']['selection']}",
        f"- {len(report['corpus']['documents'])} documents from `{report['corpus']['path']}`",
        f"- Retrieval: {pipeline['strategy']}, top {pipeline['top_k']}, "
        f"{'reranked' if pipeline['rerank'] else 'not reranked'}, {pipeline['embedding_model']}",
        f"- Model: {model}",
        f"- Code: {code}",
        "",
        "## Headline",
        "",
        "| Metric | Value (95% CI) |",
        "| --- | --- |",
        *[f"| {label} | {value} |" for label, value in _headline_rows(report)],
        "",
    ]
    by_category = report["metrics"]["by_category"]
    if "answers" in report["metrics"]["overall"]:
        lines += [
            "## By category",
            "",
            "| Category | Questions | Expected | Answered | Abstained | Failed | Correct |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        expected = {q["category"]: q["expected_action"] for q in report["questions"]}
        for category, summary in by_category.items():
            actions = summary["actions"]
            correct = summary["answers"]["correctness"]
            lines.append(
                f"| {category} | {summary['questions']} | {expected[category]} "
                f"| {actions.get('answer', 0)} | {actions.get('abstain', 0)} "
                f"| {actions.get('error', 0)} "
                f"| {correct['successes']}/{correct['n'] if correct['n'] else 0} |"
            )
        review = _review(report)
        lines += [
            "",
            "## To review",
            "",
            *(review or ["Nothing: every question got the expected behaviour."]),
            "",
        ]
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report["started_at"]).strftime("%Y%m%d-%H%M%S")
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in report["name"].lower())
    base = output_dir / f"{stamp}-{slug}"
    json_path, md_path = base.with_suffix(".json"), base.with_suffix(".md")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    return json_path, md_path
