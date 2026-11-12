# Comparison (test split)

- **e1-bm25**: retrieval, strategy sparse, top 5, not reranked; dataset 7ea28a7a3b2c, started 2026-11-12T08:22:00+05:45
- **e2-dense**: retrieval, strategy dense, top 5, not reranked; dataset 7ea28a7a3b2c, started 2026-11-12T08:22:08+05:45
- **e3-hybrid**: retrieval, strategy hybrid, top 5, not reranked; dataset 7ea28a7a3b2c, started 2026-11-12T08:22:18+05:45
- **e4-hybrid-rerank**: retrieval, strategy hybrid, top 5, reranked; dataset 7ea28a7a3b2c, started 2026-11-12T08:22:29+05:45
- **e4b-adaptive-rerank**: retrieval, strategy auto, top 5, reranked; dataset 7ea28a7a3b2c, started 2026-11-12T08:23:36+05:45

| Metric | e1-bm25 | e2-dense | e3-hybrid | e4-hybrid-rerank | e4b-adaptive-rerank |
| --- | --- | --- | --- | --- | --- |
| Questions | 29 | 29 | 29 | 29 | 29 |
| Evidence hit@k | 94% (15/16; 72%-99%) | 100% (16/16; 81%-100%) | 100% (16/16; 81%-100%) | 100% (16/16; 81%-100%) | 100% (16/16; 81%-100%) |
| Evidence recall@k | 0.94 | 0.97 | 1.00 | 1.00 | 1.00 |
| MRR@10 | 0.91 | 0.94 | 0.96 | 0.97 | 0.97 |
| nDCG@10 | 0.87 | 0.89 | 0.92 | 0.92 | 0.92 |
| Latency p50 | 4 ms | 18 ms | 22 ms | 566 ms | 562 ms |
| Latency p95 | 6 ms | 22 ms | 28 ms | 1.1 s | 1.0 s |
