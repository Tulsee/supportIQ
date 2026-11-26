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
- [`triage-t1-t4.md`](reports/triage-t1-t4.md): AI triage, T1-T5.
- [`contradictions-c1-c3.md`](reports/contradictions-c1-c3.md): the contradiction
  scanner, C1-C3.
- [`knowledge-gaps-g1-g3.md`](reports/knowledge-gaps-g1-g3.md): knowledge gaps to
  articles, G1-G3.

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

## Contradiction scanner (Module 13, C1-C3)

```bash
uv run python -m app.cli evaluate-contradictions --name c1-c3   # from backend/, ~90 min on a CPU
```

The gold labels are the six planted contradictions: the evidence of the benchmark's
`conflicting` questions, each naming two documents and the conflicting sentence in each.
C1 sweeps the candidate-pair settings (neighbours per passage, similarity floor; no model
calls). C2 asks the model about every default candidate pair in both orders, keeps the
replies in `results/contradiction-replies-<model>.jsonl` (a run can stop and resume), and
scores five decision policies on the same replies: the verdict alone, plus a confidence
floor, plus quote verification (the default), plus agreement of both orders, and quote
verification without the confidence floor. C3 is the cost of a scan. The candidate and
confidence defaults were set before the run; requiring both orders to agree became the
default after it (reported as such). The write-up, with every flagged pair checked by
hand, is [`reports/contradictions-c1-c3.md`](reports/contradictions-c1-c3.md).

## Customer assistant (Module 12, S1-S3)

```bash
uv run python -m app.cli evaluate-self-service --name s1-s3     # from backend/, ~1 h on a CPU
uv run python -m app.cli evaluate-self-service --only S1,S3     # without the 116 S2 calls
```

Customer questions go through the portal's assistant as a member with the customer
role (row-level security, public documents only, the customer policy); staff questions
through the assessed policy, as the staff Ask page. Replies are cached in
`results/self-service-replies-<model>.jsonl`. S1 measures leakage of an internal
document, with staff retrieval as the control; S2 compares the customer and staff
policies on the benchmark; S3 measures whether planted instructions in a public page
are followed, in what the customer is shown and in the model's raw output. See
`datasets/README.md` for the set and [`reports/self-service-s1-s3.md`](reports/self-service-s1-s3.md)
for the results.

## Knowledge gaps to articles (Module 13, G1-G3)

```bash
uv run python -m app.cli evaluate-gaps --name g1-g3 --regression   # from backend/, ~1.5 h on a CPU
uv run python -m app.cli evaluate-gaps --cluster-similarity 0.8     # a fixed grouping threshold
```

The Quillon documents are indexed into their own organization with 23 resolved tickets
(`datasets/knowledge_gaps/`). The 34 asked questions go through the staff pipeline, and
those it can't answer are the gaps. G1 sweeps the grouping threshold, chooses it on the
dev topics and reports the test topics. G2 drafts every gap as the worker does, and
scores its sources, key facts, checks, privacy and the planted outdated claim. G3
publishes every draft unedited and asks 22 held-out paraphrases before and after (exact
McNemar). It also rechecks each gap as the product does and, with `--regression`, asks
the benchmark's test split before and after. Every model reply is cached in
`results/gap-replies-<model>.jsonl`, keyed by the exact prompt, so a rerun replays it.
The write-up, with every draft and every held-out answer checked by hand, is
[`reports/knowledge-gaps-g1-g3.md`](reports/knowledge-gaps-g1-g3.md).
