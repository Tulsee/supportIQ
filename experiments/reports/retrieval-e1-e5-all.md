# Comparison (all questions)

- **e1-bm25**: retrieval, strategy sparse, top 5, not reranked; dataset 7ea28a7a3b2c, started 2026-11-12T08:22:00+05:45
- **e2-dense**: retrieval, strategy dense, top 5, not reranked; dataset 7ea28a7a3b2c, started 2026-11-12T08:22:08+05:45
- **e3-hybrid**: retrieval, strategy hybrid, top 5, not reranked; dataset 7ea28a7a3b2c, started 2026-11-12T08:22:18+05:45
- **e4-hybrid-rerank**: retrieval, strategy hybrid, top 5, reranked; dataset 7ea28a7a3b2c, started 2026-11-12T08:22:29+05:45
- **e4b-adaptive-rerank**: retrieval, strategy auto, top 5, reranked; dataset 7ea28a7a3b2c, started 2026-11-12T08:23:36+05:45

| Metric | e1-bm25 | e2-dense | e3-hybrid | e4-hybrid-rerank | e4b-adaptive-rerank |
| --- | --- | --- | --- | --- | --- |
| Questions | 58 | 58 | 58 | 58 | 58 |
| Evidence hit@k | 97% (31/32; 84%-99%) | 100% (32/32; 89%-100%) | 100% (32/32; 89%-100%) | 100% (32/32; 89%-100%) | 100% (32/32; 89%-100%) |
| Evidence recall@k | 0.97 | 0.98 | 1.00 | 1.00 | 1.00 |
| MRR@10 | 0.89 | 0.93 | 0.95 | 0.96 | 0.96 |
| nDCG@10 | 0.87 | 0.91 | 0.92 | 0.93 | 0.93 |
| Latency p50 | 4 ms | 18 ms | 21 ms | 855 ms | 789 ms |
| Latency p95 | 7 ms | 29 ms | 32 ms | 1.2 s | 1.1 s |
