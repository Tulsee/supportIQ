# Evaluation report: Quillon support benchmark v1

12 November 2026 · dataset `quillon_support_v1.jsonl` (sha256 `7ea28a7a3b2c`) · code `259fd1b`

This is the evaluation the implementation plan asks for in Module 8: the retrieval
experiments E1 to E5 and the effect of evidence assessment (E6), with confidence
intervals, error analysis and the limits of what the numbers show. Every figure comes
from a result file in `../results/` (listed at the end) and can be reproduced with the
commands in [Reproducing](#reproducing).

## Summary

- **Retrieval is near the ceiling of this dataset.** Every configuration except BM25 on
  its own puts labelled evidence in the top 5 for all 32 answerable questions. Ranking
  quality rises from BM25 (MRR@10 0.89, nDCG@10 0.87) through dense (0.93, 0.91) and
  fixed hybrid (0.95, 0.92) to hybrid with reranking (0.96, 0.93). Adaptive fusion with
  reranking scores the same as fixed hybrid with reranking. The steps are small next to
  the confidence intervals, and reranking adds about 0.8 s per query on a laptop CPU.
- **Evidence assessment stops the assistant answering what it shouldn't.** On the 29
  held-out test questions, the always-answer baseline (E5) answered 8 of the 13
  questions that should have been clarified, escalated or declined. With the decision
  policy (E6) it answered none of them (0/13, 95% CI 0-23%; exact McNemar p = 0.008).
  Decision accuracy rose from 72% to 93% (21/29 to 27/29), but with 29 questions that
  difference is not significant at the 5% level (p = 0.07).
- **The cost was one answerable question**, escalated because two documents disagree on
  it (q004, see [Error analysis](#error-analysis)). In both runs, every answer to an
  answerable question contained every key fact.
- **The evidence score is not a probability.** It ranks evidence quality, but as a
  forecast of "handled correctly" it does worse (Brier 0.13) than always predicting the
  base rate (0.064).
- **It is slower.** E6's median latency is 31.8 s against 27.7 s, mostly because its
  prompt is longer; 4 of the 29 questions were settled in about a second without asking
  the model.
- **This does not show the system never makes things up.** 0 unsupported answers out of
  15 is consistent with a true rate of up to 20%, and the dataset is small and was
  written alongside the system ([Limitations](#limitations-and-threats-to-validity)).

## 1. Setup

### Dataset

Ten synthetic support documents for a fictional company (Quillon: mesh routers, indoor
cameras, a security subscription) with six deliberate contradictions, mostly between an
outdated 2024 FAQ and the current policy pages. 58 questions, each labelled with the
action an ideal assistant takes, exact evidence quotes and the key facts a correct
answer must state. Provenance and labelling rules are in
[`datasets/README.md`](../../datasets/README.md).

| Category | Expected action | Dev | Test |
| --- | --- | --- | --- |
| Factual | answer | 5 | 5 |
| Paraphrase | answer | 4 | 4 |
| Identifier (error codes, versions) | answer | 4 | 4 |
| Multi-part | answer | 3 | 3 |
| Ambiguous | clarify | 3 | 3 |
| Conflicting | escalate | 3 | 3 |
| Insufficient (topic covered, fact not) | abstain | 3 | 3 |
| Unanswerable | abstain | 4 | 4 |
| **Total** | | **29** | **29** |

Questions alternate between the two splits within each category. Decision thresholds
were tuned on **dev** only, and answer results are reported on **test**. Retrieval
settings were fixed in Module 4, before the dataset existed, so retrieval is reported on
all 58 questions (the test-only table, `retrieval-e1-e5-test.md`, ranks the
configurations the same way).

### System and controls

| Setting | Value |
| --- | --- |
| Chunking | 480 tokens, 72 overlap |
| Embeddings | BAAI/bge-small-en-v1.5 (FastEmbed, Qdrant) |
| Keyword search | BM25 in PostgreSQL, k1 = 1.2, b = 0.75 |
| Fusion | Reciprocal Rank Fusion, k = 60, 20 candidates per retriever |
| Reranker | ms-marco-MiniLM-L-6-v2 cross-encoder, top 20 reranked |
| Passages in the prompt | 5 |
| Model | qwen3.5:4b (Q4_K_M, digest `2a654d98e6fb`) via Ollama 0.40.16 |
| Sampling | temperature 0.1, seed 42, 8192-token context, thinking off |
| Hardware | Intel Core i5-1135G7 (4 cores), 16 GB RAM, CPU only |

Held constant across runs: the dataset and its hash, the indexed documents (not
re-indexed between runs), the model digest, the seed, the machine and the code commit.
E5 and E6 ran back to back.

**What differs between E5 and E6.** E6 uses a different prompt
(`grounded-answer-v2-structured`, which asks the model to report whether it answered,
found nothing, saw a conflict or needs clarification) and the decision policy that acts
on that report and on the retrieval signals. The self-report is part of the evidence
assessment by design, so the comparison measures prompt and policy together, not the
policy's thresholds alone.

## 2. Retrieval (E1-E5)

All 58 questions; the retrieval metrics count the 32 answerable ones (the others have no
evidence to find).

| Metric | E1 BM25 | E2 Dense | E3 Hybrid | E4 Hybrid + rerank | E5 Adaptive + rerank |
| --- | --- | --- | --- | --- | --- |
| Evidence in top 5 | 97% (31/32; 84-99%) | 100% (32/32; 89-100%) | 100% (32/32; 89-100%) | 100% (32/32; 89-100%) | 100% (32/32; 89-100%) |
| Evidence recall@5 | 0.97 | 0.98 | 1.00 | 1.00 | 1.00 |
| MRR@10 | 0.89 | 0.93 | 0.95 | 0.96 | 0.96 |
| nDCG@10 | 0.87 | 0.91 | 0.92 | 0.93 | 0.93 |
| Latency p50 / p95 | 4 / 7 ms | 18 / 29 ms | 21 / 32 ms | 855 ms / 1.2 s | 789 ms / 1.1 s |

MRR@10 by category:

| Category | E1 BM25 | E2 Dense | E3 Hybrid | E4 Hybrid + rerank | E5 Adaptive + rerank |
| --- | --- | --- | --- | --- | --- |
| Factual (10) | 1.00 | 0.90 | 1.00 | 1.00 | 1.00 |
| Paraphrase (8) | 0.69 | **0.92** | 0.79 | 0.84 | 0.84 |
| Identifier (8) | 0.94 | 0.94 | 1.00 | 1.00 | 1.00 |
| Multi-part (6) | 0.92 | 1.00 | 1.00 | 1.00 | 1.00 |

What the runs show:

- **Keyword search fails on paraphrases.** BM25 never finds the evidence for "Who pays
  the postage when I send back something that's broken?" (q018), because the document
  says "return shipping is free for defective products" and shares no words with it.
- **Dense search wins on paraphrases, and later stages give some of that back.** Dense
  alone reaches 0.92 on paraphrases. Fixed hybrid drops to 0.79: fusing in BM25's
  ranking moves q018's evidence from rank 1 to 3. The adaptive strategy already sends
  all 8 paraphrases to dense-heavy weights (dense 1.0, keyword 0.3), but the reranker
  sets the final order, and on these questions it is weaker than dense search: it moves
  q013's evidence from rank 2 to 5. With reranking, fixed and adaptive hybrid both end
  at 0.84. Keeping the dense order for paraphrases might recover the gap, but 8
  questions can't support that change; it needs a larger set.
- **Fusion fixes what each retriever misses on its own.** BM25 alone ranks the evidence
  second for "What does error E-7001 mean?" (q020) and third for q015; dense alone ranks
  it second for "What changed in firmware 4.1.3?" (q026), q004 and q007. Every fused
  configuration ranks all of these first, except q015 without reranking (second).
- **Multi-part questions have the lowest nDCG (about 0.78)** in every configuration,
  because their evidence spans several passages that don't all reach the top ranks; the
  first piece of evidence is still found at rank 1.
- **Reranking costs about 0.8 s** per question on this CPU and buys about 0.01 of MRR
  here. On a dataset this close to the ceiling that is not enough to justify or rule
  out the reranker.

## 3. Answers and decisions (E5 vs E6, test split)

| Metric | E5 always answer | E6 evidence policy |
| --- | --- | --- |
| Decision accuracy (did what the label expects) | 72% (21/29; 54-85%) | 93% (27/29; 78-98%) |
| Unwarranted answers (should not have answered) | 62% (8/13; 36-82%) | 0% (0/13; 0-23%) |
| Unsupported answers (unwarranted or missing a key fact) | 33% (8/24; 18-53%) | 0% (0/15; 0-20%) |
| Coverage (answerable questions answered) | 100% (16/16; 81-100%) | 94% (15/16; 72-99%) |
| Correctness (answers with every key fact) | 100% (16/16; 81-100%) | 100% (15/15; 80-100%) |
| Abstention precision | 100% (5/5; 57-100%) | 100% (8/8; 68-100%) |
| Abstention recall | 71% (5/7; 36-92%) | 100% (7/7; 65-100%) |
| Citation precision | 85% (17/20; 64-95%) | 89% (16/18; 67-97%) |
| Latency p50 / p95 | 27.7 s / 41.2 s | 31.8 s / 49.7 s |

Paired on the same 29 questions (exact McNemar test on the questions where the two runs
differ):

| Outcome | E6 better | E6 worse | p |
| --- | --- | --- | --- |
| Handled as labelled (and correct if answered) | 7 | 1 | 0.07 |
| Not answering when it shouldn't (13 questions) | 8 | 0 | 0.008 |
| Answering an answerable question correctly (16) | 0 | 1 | 1.0 |

What each run did, by category (test split):

| Category | Expected | E5 | E6 |
| --- | --- | --- | --- |
| Factual, paraphrase, identifier, multi-part (16) | answer | 16 answered | 15 answered, 1 escalated (q004) |
| Ambiguous (3) | clarify | 3 answered | 2 clarified, 1 declined (q038) |
| Conflicting (3) | escalate | 3 answered | 3 escalated |
| Insufficient (3) | abstain | 2 answered, 1 declined | 3 declined |
| Unanswerable (4) | abstain | 4 declined | 4 declined |

What the baseline's 8 unwarranted answers actually were matters for reading these
numbers:

- **Ambiguous questions answered with one reading.** "How long do I have?" got "You
  have 60 days to return your Quillon product for a full refund": the outdated FAQ's
  figure, presented as fact. E6 asked which topic was meant.
- **Conflicts answered without saying so.** For "How long does it take to get my
  refund?" E5 gave both figures (14 business days and 5-7 business days) as if both
  applied. E6 escalated with the same two statements, each cited, for a person to
  decide.
- **Two "answers" were already honest.** For q046 and q048 E5 said the documents don't
  cover the question, then added what they do say; the scoring counts these as answers
  because they cite passages.

So most of the gain is in routing (asking, or handing to a person) rather than in
removing invented content: the baseline's failures were mostly outdated or one-sided
statements from the documents presented as the answer. Whether any answer also made
claims beyond its sources isn't measured (see the limitations).

## 4. Tuning the decision policy (dev split)

1. **Observe run** on the 29 dev questions: the policy decides, but the model is always
   asked, so every signal is recorded for every question.
2. **Offline replay** of the policy over a grid of thresholds (no model calls).
3. **Choice:** the highest decision accuracy; ties broken by fewer unsupported answers,
   more correct answers, fewer model calls, then the loosest thresholds.

The initial thresholds reached 97% on dev. **24 settings tied at 100%**, so the
dev split could not tell them apart; the tie-break picked a coverage floor of 0.3 (it
skips the model on 5 dev questions instead of 2), no relevance floor, partial answers
allowed, and the claim-check thresholds switched off. What the dev run showed about the
signals:

- **Rerank relevance is a poor gate.** Its sigmoid score is near zero for correctly
  answered paraphrases (q018 on test: 0.005), so any floor high enough to matter blocks
  good answers.
- **The NLI claim checker (nli-deberta-v3-xsmall) was unreliable here.** It scored
  supported claims as unsupported, flagged near-duplicate wording as contradictions,
  and missed a real conflict. Conflicts are caught by the model's self-report instead.
  The checker's scores are still recorded and shown in the inspector, and they feed the
  evidence score, but they no longer change decisions.
- **The vagueness rule** (a pronoun-only subject, or no content words) flags 4 of the 6
  ambiguous questions in the whole dataset with no false positives. It was written after
  reading the dataset, test questions included; see the limitations.

## 5. Calibration

The evidence score (a weighted mix of relevance, coverage, consistency and grounding)
was compared with "handled as labelled, and correct if answered" on the 29 test
questions: Brier 0.13, expected calibration error 0.17 (5 bins). A constant forecast of
the base rate (27/29) scores 0.064, so as a probability the score is worse than no
model. That is expected from what it measures: a correct abstention has weak evidence and
a low score, yet it is a success. The score is shown as a ranking aid, not as "the
chance this answer is right". A calibrated confidence would need a model fitted on dev
data from these signals to the outcome, and with 2 failures in 29 questions there is not
enough data to fit or check one.

## 6. Latency and resources

| | p50 | p95 |
| --- | --- | --- |
| Retrieval only, no reranking (E1-E3) | 4-21 ms | 7-32 ms |
| Retrieval with reranking (E4, E5) | 0.8 s | 1.1-1.2 s |
| Answer, always answer (E5) | 27.7 s | 41.2 s |
| Answer with evidence policy (E6) | 31.8 s | 49.7 s |
| E6, decided before asking the model (4 of 29) | 0.6-1.2 s | |

- **Where E6's time goes.** Retrieval, the citation checks and the claim checks together
  take about 1 s per question in both runs. The rest is the model. The structured prompt
  is longer (about 864 input tokens against 672), and on a CPU reading the prompt is
  most of the work. Answers are also slightly longer (68 output tokens against 56).
- **Memory.** The model keeps about 3.4 GB resident while loaded; the embedding,
  reranking and claim-check models add a few hundred MB to the API process.
- **Hardware.** The numbers come from a laptop CPU shared with the development servers
  and a browser, so they show relative cost, not production latency.

## Error analysis

- **q004, escalated instead of answered ("What is the restocking fee for opened
  products?").** The returns policy says opened products can be returned within 14 days
  for a 15% restocking fee. The outdated FAQ says any product can be returned within 60
  days "for a full refund, no questions asked". The model reported a conflict and quoted
  both, with citations; its draft contained the right fee. The label is debatable: the
  dataset's own rule is to escalate when documents conflict, and these do. It stays as
  labelled here, because changing test labels after seeing results is what the split is
  meant to prevent. It is a candidate for blind relabelling in v2.
- **q038, declined instead of clarified ("What does the error code mean?").** The
  question has content words ("error code"), so the vagueness rule doesn't fire.
  Retrieval found the error code reference (relevance 0.99), and the model, given
  several codes and no way to pick one, reported "not found". A person still gets the
  question, so the outcome is safe but not ideal. A possible rule: a definite reference
  ("the error code") whose passages list several distinct candidates is ambiguous. It
  needs more ambiguous questions to test without overfitting.
- **Citation precision (16/18 in E6, 17/20 in E5)** is measured against the labelled
  evidence quotes. Of E6's two misses, q018 cites the warranty passage for "we email you
  a prepaid label", which is correct support that the label doesn't list, and q004 is
  the escalation above. The metric understates precision when a document supports a
  claim in more than one place.
- **Retrieval:** see section 2 (q018 with BM25, q013 after fusion and reranking).

## Limitations and threats to validity

- **Small sample.** 29 test questions, and 3 to 5 per category. Most differences
  between retrieval configurations, and the decision accuracy gain, are within the
  confidence intervals.
- **Written alongside the system.** The documents and questions were written in this
  project, by the same process that built the pipeline. Questions are probably closer
  to the documents' wording than real customer messages, which flatters retrieval.
- **Some rules were designed with the whole dataset in view.** The vagueness rule and
  the decision to rely on the model's self-report for conflicts came from inspecting
  failures across the dataset, test split included. Test results for the ambiguous and
  conflicting categories are therefore optimistic. A fresh, blind test set is the fix.
- **Tuning could not choose.** 24 threshold settings tied on dev, so the chosen
  thresholds are one of many that fit 29 questions, and may not transfer.
- **One run per configuration.** Generation is seeded with temperature 0.1, and E5
  reproduced the earlier baseline exactly, but variation across seeds, models and
  prompt wording was not measured.
- **Proxy metrics.** Correctness is key-fact matching: it catches missing and wrong
  facts but not extra unsupported claims. Faithfulness and answer relevance, which the
  plan lists, were not scored by a judge model; the NLI claim checks are a partial
  proxy and proved unreliable.
- **One language and domain.** English consumer-electronics support only. The Nepali
  OCR added in this module is not covered by this benchmark.
- **Shared hardware.** Latency was measured on a laptop CPU with other programs running.

## Next steps

1. Grow the dataset towards the plan's 150-300 questions, with questions written by
   someone who hasn't seen the pipeline, split by document family, and a blind relabel
   of disputed items such as q004.
2. With more ambiguous and paraphrase questions, test the two rule changes suggested
   above (definite references with several candidates; keeping the dense order over
   the reranker for paraphrases) on dev, then once on test.
3. Add a judged faithfulness metric (a stronger model as judge, checked against a
   human-labelled sample) to cover the gap key-fact matching leaves.
4. Repeat E5 and E6 with several seeds and a second model (Claude through
   `LLM_PROVIDER=anthropic`) to separate the policy's effect from one model's behaviour.

## Reproducing

From `backend/`, with the Docker services running and the model pulled:

```bash
# Retrieval (seconds each)
uv run python -m app.cli evaluate --retrieval-only --strategy sparse --no-rerank --name e1-bm25
uv run python -m app.cli evaluate --retrieval-only --strategy dense --no-rerank --name e2-dense
uv run python -m app.cli evaluate --retrieval-only --strategy hybrid --no-rerank --name e3-hybrid
uv run python -m app.cli evaluate --retrieval-only --strategy hybrid --name e4-hybrid-rerank
uv run python -m app.cli evaluate --retrieval-only --name e4b-adaptive-rerank

# Policy tuning on dev
uv run python -m app.cli evaluate --policy observe --split dev --name observe-dev
uv run python -m app.cli tune-policy --report ../experiments/results/<observe-dev>.json

# Answers on test (about 15 minutes each on a laptop CPU)
uv run python -m app.cli evaluate --policy baseline --split test --name e5-baseline-test
uv run python -m app.cli evaluate --policy assessed --split test --name e6-assessed-test

# Tables
uv run python -m app.cli compare ../experiments/results/<e5>.json ../experiments/results/<e6>.json --split test
```

Result files (`../results/`):

| Run | Files |
| --- | --- |
| E1 | `20261112-082200-e1-bm25.{md,json}` |
| E2 | `20261112-082208-e2-dense.{md,json}` |
| E3 | `20261112-082218-e3-hybrid.{md,json}` |
| E4 | `20261112-082229-e4-hybrid-rerank.{md,json}` |
| E5 retrieval | `20261112-082336-e4b-adaptive-rerank.{md,json}` |
| Dev observe run and tuning | `20261112-032904-observe-dev.{md,json}`, `20261112-032904-observe-dev-tuned-dev.json` |
| E5 answers (test) | `20261112-084049-e5-baseline-test.{md,json}` |
| E6 answers (test) | `20261112-082435-e6-assessed-test.{md,json}` |
| First baseline (all questions, commit `f769d31`) | `20261111-230702-baseline-qwen3-5-4b.{md,json}` |

Generated comparison tables: `retrieval-e1-e5-all.md`, `retrieval-e1-e5-test.md`,
`answers-e5-e6-test.md`. E6's result file records "uncommitted changes": the only
uncommitted change at the time was to how `compare` formats latencies, which doesn't
affect a run.
