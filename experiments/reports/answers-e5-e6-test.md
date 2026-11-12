# Comparison (test split)

- **e5-baseline-test**: answer, strategy auto, top 5, reranked, policy baseline, qwen3.5:4b (grounded-answer-v1); dataset 7ea28a7a3b2c, started 2026-11-12T08:40:49+05:45
- **e6-assessed-test**: answer, strategy auto, top 5, reranked, policy assessed, qwen3.5:4b (grounded-answer-v2-structured); dataset 7ea28a7a3b2c, started 2026-11-12T08:24:35+05:45

| Metric | e5-baseline-test | e6-assessed-test |
| --- | --- | --- |
| Questions | 29 | 29 |
| Evidence hit@k | 100% (16/16; 81%-100%) | 100% (16/16; 81%-100%) |
| Evidence recall@k | 1.00 | 1.00 |
| MRR@10 | 0.97 | 0.97 |
| nDCG@10 | 0.92 | 0.92 |
| Coverage (answerable answered) | 100% (16/16; 81%-100%) | 94% (15/16; 72%-99%) |
| Correctness (key facts) | 100% (16/16; 81%-100%) | 100% (15/15; 80%-100%) |
| Unsupported answers | 33% (8/24; 18%-53%) | 0% (0/15; 0%-20%) |
| Unwarranted answers | 62% (8/13; 36%-82%) | 0% (0/13; 0%-23%) |
| Abstention precision | 100% (5/5; 57%-100%) | 100% (8/8; 68%-100%) |
| Abstention recall | 71% (5/7; 36%-92%) | 100% (7/7; 65%-100%) |
| Decision accuracy | 72% (21/29; 54%-85%) | 93% (27/29; 78%-98%) |
| Citation precision | 85% (17/20; 64%-95%) | 89% (16/18; 67%-97%) |
| Evidence score Brier (lower is better) | - | 0.13 |
| Evidence score ECE (lower is better) | - | 0.17 |
| Latency p50 | 27.7 s | 31.8 s |
| Latency p95 | 41.2 s | 49.7 s |
