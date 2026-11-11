# Evaluation: retrieval-auto-reranked

- Started 2026-11-11T23:05:55+05:45, took 52 s
- Dataset `datasets/evaluation_questions/quillon_support_v1.jsonl` (58 questions, sha256 7ea28a7a3b2c), selection {'split': 'all', 'only': None, 'limit': None}
- 10 documents from `datasets/sample_documents/quillon`
- Retrieval: auto, top 5, reranked, BAAI/bge-small-en-v1.5
- Model: retrieval only (no model)
- Code: 1a45fbbfb3 (uncommitted changes)

## Headline

| Metric | Value (95% CI) |
| --- | --- |
| Evidence hit@5 | 100% (32/32; 89%-100%) |
| Evidence recall@5 | 1.00 |
| MRR@10 | 0.96 |
| Latency p50 / p95 | 1.0 s / 1.4 s |

