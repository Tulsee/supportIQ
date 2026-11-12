"""Management commands.

    uv run python -m app.cli grant-superadmin admin@example.com
    uv run python -m app.cli revoke-superadmin admin@example.com
    uv run python -m app.cli purge-expired
    uv run python -m app.cli export-openapi --output openapi.json
    uv run python -m app.cli evaluate --name baseline
    uv run python -m app.cli tune-policy --report experiments/results/<observe run>.json
    uv run python -m app.cli compare experiments/results/a.json experiments/results/b.json

Super admin can only be bootstrapped here (there is no API to grant it from
scratch); afterwards super admins can manage each other via /api/v1/admin.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.database import SessionLocal, engine
from app.core.exceptions import NotFoundError
from app.services.maintenance import purge_expired
from app.services.platform_admin import PlatformAdminService


async def _set_superadmin(email: str, value: bool) -> int:
    async with SessionLocal() as session:
        try:
            user = await PlatformAdminService(session).set_superadmin(email.lower(), value)
        except NotFoundError as exc:
            print(exc.message, file=sys.stderr)
            return 1
    print(f"{user.email}: super admin = {user.is_superadmin}")
    return 0


async def _purge_expired() -> int:
    async with SessionLocal() as session:
        result = await purge_expired(session)
    print(
        f"Deleted {result.refresh_tokens} expired refresh tokens "
        f"and {result.invitations} stale invitations; "
        f"marked {result.stalled_ingestions} stalled document ingestions as failed"
    )
    return 0


def _export_openapi(output: Path | None) -> int:
    """Write the API contract (no server or database needed). The frontend's typed
    client is generated from it, and CI checks that the two stay in sync."""
    from app.main import app  # imported lazily: only this command needs the app

    document = json.dumps(app.openapi(), indent=2) + "\n"
    if output is None:
        sys.stdout.write(document)
    else:
        output.write_text(document, encoding="utf-8")
    return 0


async def _evaluate(args: argparse.Namespace) -> int:
    """Run an evaluation dataset through the pipeline and write a report."""
    from datetime import datetime

    from ai_engine.evaluation.dataset import DatasetError, load_dataset
    from ai_engine.evaluation.metrics import QuestionOutcome
    from ai_engine.retrieval.engine import RequestedStrategy
    from app.core.config import settings
    from app.core.knowledge import (
        get_embedder,
        get_nli_model,
        get_reranker,
        new_qdrant_client,
        vector_store_for,
    )
    from app.core.llm import build_llm_client
    from app.core.storage import get_object_storage
    from app.evaluation.report import build_report, write_report
    from app.evaluation.runner import EvaluationError, EvaluationRunner, RunOptions
    from app.services.ingestion import IngestionLimits

    try:
        dataset = load_dataset(args.dataset)
    except (OSError, DatasetError) as exc:
        print(f"Can't read the dataset: {exc}", file=sys.stderr)
        return 1
    ids = set(args.only.split(",")) if args.only else None
    questions = dataset.select(split=args.split, ids=ids, limit=args.limit)
    if not questions:
        print("No questions match the selection.", file=sys.stderr)
        return 1
    options = RunOptions(
        strategy=RequestedStrategy(args.strategy),
        rerank=not args.no_rerank,
        top_k=args.top_k,
        retrieval_only=args.retrieval_only,
        policy=args.policy,
    )
    llm = build_llm_client(settings.model_copy(update={"ollama_seed": args.seed}))
    if not options.retrieval_only:
        status = await llm.status()
        if not status.available:
            print(f"The answer model isn't ready: {status.detail}", file=sys.stderr)
            return 1

    def progress(number: int, total: int, outcome: QuestionOutcome) -> None:
        q = outcome.question
        verdict = ""
        if outcome.status is not None:
            verdict = f"{outcome.status:<11} expected {q.expected_action:<8}"
            if q.answerable and outcome.action == "answer":
                missing = outcome.missing_facts
                verdict += " MISSING " + ", ".join(missing) if missing else " correct"
        rank = outcome.evidence_rank if q.evidence else None
        print(
            f"[{number:>3}/{total}] {q.id} {q.category:<12} {verdict} "
            f"(evidence rank {rank or '-'}, {outcome.latency_ms / 1000:.1f} s)",
            flush=True,
        )

    embedder = get_embedder()
    qdrant = new_qdrant_client()
    started = datetime.now().astimezone()
    try:
        async with SessionLocal() as session:
            runner = EvaluationRunner(
                session,
                storage=get_object_storage(),
                embedder=embedder,
                vector_store=vector_store_for(qdrant, embedder),
                reranker=get_reranker(),
                llm=llm,
                limits=IngestionLimits.from_settings(),
                nli=get_nli_model() if args.policy != "baseline" else None,
            )
            try:
                workspace = await runner.prepare(args.documents, share_with=args.share_with)
            except EvaluationError as exc:
                print(exc, file=sys.stderr)
                return 1
            indexed_now = sum(d.indexed_now for d in workspace.documents)
            mode = " (retrieval only)" if options.retrieval_only else f" with {llm.model}"
            print(
                f"{len(workspace.documents)} documents ready ({indexed_now} indexed now) in "
                f"organization {workspace.organization.id}; {len(questions)} questions{mode}",
                flush=True,
            )
            outcomes = await runner.run(workspace, questions, options, progress=progress)
    finally:
        await qdrant.close()

    report = build_report(
        name=args.name,
        dataset=dataset,
        questions=questions,
        selection={"split": args.split, "only": sorted(ids) if ids else None, "limit": args.limit},
        outcomes=outcomes,
        options=options,
        workspace=workspace,
        corpus=args.documents,
        llm=llm,
        started_at=started,
        finished_at=datetime.now().astimezone(),
    )
    json_path, md_path = write_report(report, args.output_dir)
    print(f"Report: {md_path}")
    print(f"Data:   {json_path}")
    return 0


def _compare(args: argparse.Namespace) -> int:
    from app.evaluation.compare import compare

    try:
        table = compare(args.reports, args.split)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Can't compare: {exc}", file=sys.stderr)
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(table, encoding="utf-8")
        print(f"Written: {args.output}")
    else:
        sys.stdout.write(table)
    return 0


def _tune_policy(args: argparse.Namespace) -> int:
    from app.evaluation.tuning import env_lines, tune_file

    try:
        result = tune_file(args.report, args.split)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Can't tune from {args.report}: {exc}", file=sys.stderr)
        return 1
    best, default = result["best"], result["defaults"]
    print(f"{result['questions']} {result['split']} questions from {result['source_run']}")
    for label, r in (("defaults", default), ("best", best)):
        print(
            f"{label:<9} accuracy {r['accuracy']:.0%}, {r['answered']} answered "
            f"({r['answered_correct']} correct and warranted), {r['unsupported']} unsupported, "
            f"{r['skipped_generations']} decided without the model"
        )
    print(f"({result['equally_good']} settings tie; shown: fewest model calls, then loosest)")
    print("\n".join(env_lines(best["thresholds"])))
    output = args.report.with_name(args.report.stem + f"-tuned-{args.split}.json")
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Written: {output}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    try:
        if args.command == "tune-policy":
            return _tune_policy(args)
        if args.command == "compare":
            return _compare(args)
        if args.command == "evaluate":
            return await _evaluate(args)
        if args.command == "grant-superadmin":
            return await _set_superadmin(args.email, True)
        if args.command == "revoke-superadmin":
            return await _set_superadmin(args.email, False)
        return await _purge_expired()
    finally:
        await engine.dispose()


def _add_evaluate(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    from app.core.config import REPO_ROOT

    datasets = REPO_ROOT / "datasets"
    evaluate = commands.add_parser(
        "evaluate",
        help="Run labelled questions through the pipeline and write a report",
        description="Index a document folder into an evaluation organization, run each "
        "labelled question through retrieval and answer generation, and write metrics "
        "(JSON and Markdown). Every question is recorded as a run in the AI inspector.",
    )
    evaluate.add_argument("--name", default="run", help="Label for the report (default: run)")
    evaluate.add_argument(
        "--dataset",
        type=Path,
        default=datasets / "evaluation_questions" / "quillon_support_v1.jsonl",
        help="Questions (JSON Lines)",
    )
    evaluate.add_argument(
        "--documents",
        type=Path,
        default=datasets / "sample_documents" / "quillon",
        help="Folder of documents the questions are about",
    )
    evaluate.add_argument("--split", choices=["all", "dev", "test"], default="all")
    evaluate.add_argument("--only", help="Comma-separated question ids")
    evaluate.add_argument("--limit", type=int, help="At most this many questions")
    evaluate.add_argument(
        "--retrieval-only", action="store_true", help="Score retrieval only (no model, fast)"
    )
    evaluate.add_argument(
        "--strategy", choices=["auto", "hybrid", "sparse", "dense"], default="auto"
    )
    evaluate.add_argument("--top-k", type=int, default=5, choices=range(1, 11), metavar="1-10")
    evaluate.add_argument("--no-rerank", action="store_true", help="Skip the cross-encoder")
    evaluate.add_argument(
        "--policy",
        choices=["assessed", "baseline", "observe"],
        default="assessed",
        help="assessed: the evidence policy; baseline: always answer; observe: the "
        "policy, but always ask the model (records every signal, for tune-policy)",
    )
    evaluate.add_argument("--seed", type=int, default=42, help="Ollama sampling seed")
    evaluate.add_argument(
        "--share-with",
        metavar="EMAIL",
        help="Add this registered user to the evaluation organization, to browse its runs",
    )
    evaluate.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "experiments" / "results",
        help="Where reports are written",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    grant = commands.add_parser("grant-superadmin", help="Make a registered user a super admin")
    grant.add_argument("email")
    revoke = commands.add_parser("revoke-superadmin", help="Remove super admin from a user")
    revoke.add_argument("email")
    commands.add_parser("purge-expired", help="Delete expired refresh tokens and stale invitations")
    export = commands.add_parser("export-openapi", help="Write the OpenAPI document as JSON")
    export.add_argument("--output", type=Path, help="File to write (default: stdout)")
    _add_evaluate(commands)
    tune = commands.add_parser(
        "tune-policy",
        help="Choose evidence-policy thresholds from an observe run",
        description="Replay the decision policy over the signals recorded by "
        "`evaluate --policy observe` under a grid of thresholds and report the best on "
        "the chosen split (no model calls).",
    )
    tune.add_argument("--report", type=Path, required=True, help="An observe run's JSON")
    tune.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    comparison = commands.add_parser(
        "compare", help="Compare evaluation runs side by side (Markdown table)"
    )
    comparison.add_argument("reports", type=Path, nargs="+", help="Run JSON files")
    comparison.add_argument("--split", choices=["all", "dev", "test"], default="all")
    comparison.add_argument("--output", type=Path, help="File to write (default: stdout)")
    args = parser.parse_args(argv)
    if args.command == "export-openapi":
        return _export_openapi(args.output)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
