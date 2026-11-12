# Experiments

Each evaluation run writes two files to `results/`, named by start time and run name:

- `<time>-<name>.md`: a summary with the headline metrics (95% confidence intervals),
  a per-category table, and the questions worth reviewing, each with the id of its
  run in the AI inspector.
- `<time>-<name>.json`: everything needed to reproduce and compare the run: the
  dataset and its hash, the document hashes, every retrieval and model setting, the
  decision policy and its thresholds, the model version, package versions, the git
  commit, and each question's outcome and evidence signals.

Runs are made with `uv run python -m app.cli evaluate` from `backend/`; see
`datasets/README.md` for the dataset and the options. Compare runs only when the
dataset hash matches (`compare` warns otherwise). A run made with uncommitted changes
says so in its summary.

## The plan's experiments (E1-E6)

| Id | Configuration | Command (from `backend/`) |
| --- | --- | --- |
| E1 | BM25 only | `evaluate --retrieval-only --strategy sparse --no-rerank --name e1-bm25` |
| E2 | Dense retrieval only | `evaluate --retrieval-only --strategy dense --no-rerank --name e2-dense` |
| E3 | Fixed hybrid | `evaluate --retrieval-only --strategy hybrid --no-rerank --name e3-hybrid` |
| E4 | Hybrid + reranking | `evaluate --retrieval-only --strategy hybrid --name e4-hybrid-rerank` |
| E5 | Adaptive hybrid + reranking (answers: always answer) | `evaluate --policy baseline --name e5-baseline` |
| E6 | E5 + uncertainty-aware decisions | `evaluate --policy assessed --name e6-assessed` |

E1-E4 score retrieval only (about a minute each). E5 and E6 generate answers with the
configured model (about 30 s per question on a laptop CPU with qwen3.5:4b).

Compare them with
`uv run python -m app.cli compare results/<e1>.json ... --split test --output reports/<name>.md`.

## Tuning the decision policy (Module 5)

Thresholds are chosen on the **dev** split and results are reported on the **test**
split:

1. `evaluate --policy observe --split dev --name observe-dev`: the policy runs, but the
   model is always asked, so every signal is recorded for every question.
2. `tune-policy --report results/<observe-dev>.json`: replays the policy over the
   recorded signals under a grid of thresholds (no model calls) and prints the best
   (highest decision accuracy; ties go to fewer unsupported answers, more correct
   answers, fewer model calls, then the loosest thresholds). Put them in `.env`, or
   in the defaults in `app/core/config.py`.
3. `evaluate --policy assessed --split test --name assessed-test`, and compare it with
   the baseline's test split.

Reports of finished studies are in `reports/`:

- [`quillon-v1-evaluation.md`](reports/quillon-v1-evaluation.md): E1-E6 on the Quillon
  benchmark v1 (retrieval, decisions, calibration, latency, error analysis and
  limitations), with the generated comparison tables beside it.

## AI triage (Module 11, T1-T4)

```bash
uv run python -m app.cli evaluate-triage --name t1-t4          # from backend/
uv run python -m app.cli evaluate-triage --alpha 0.5 --threshold 0.7 --name fixed
```

The model is asked once per ticket and its replies are kept in
`results/triage-replies-<model>.jsonl`, so a run can stop and resume, and re-tuning or
a fixed-policy run costs no model calls. T1 is the model alone, T2 the neighbour vote
alone, T3 both combined always applying, T4 both combined with abstention; α and the
threshold are chosen on dev (the most coverage at 95% dev accuracy) and everything is
reported on test. The write-up is `reports/triage-t1-t4.md`.
