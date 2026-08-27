# Production-equivalent retrieval benchmark

The benchmark uses the locked runtime split. Evidence metrics are reported in JSON but are not used for model selection.

| Split | Method | Article Recall@5 | All-Article Hit@5 | Article MRR@5 | Multi-Article Recall@5 | p50 ms | p95 ms |
|---|---|---:|---:|---:|---:|---:|---:|
| dev | dense | 0.7228 | 0.5161 | 0.8817 | 0.5606 | 68.3 | 124.0 |
| dev | sparse | 0.7659 | 0.5806 | 0.8129 | 0.5161 | 1.1 | 1.7 |
| dev | hybrid | 0.8048 | 0.6129 | 0.9274 | 0.5967 | 64.7 | 104.6 |

> This artifact contains development-split comparison only. The locked held-out test result is maintained separately.
