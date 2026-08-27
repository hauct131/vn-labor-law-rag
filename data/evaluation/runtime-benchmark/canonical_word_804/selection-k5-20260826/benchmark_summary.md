# Production-equivalent retrieval benchmark

The benchmark uses the locked runtime split. Evidence metrics are reported in JSON but are not used for model selection.

| Split | Method | Article Recall@5 | All-Article Hit@5 | Article MRR@5 | Multi-Article Recall@5 | p50 ms | p95 ms |
|---|---|---:|---:|---:|---:|---:|---:|
| dev | dense | 0.7228 | 0.5161 | 0.8817 | 0.5606 | 68.4 | 114.1 |
| dev | sparse | 0.7659 | 0.5806 | 0.8129 | 0.5161 | 1.0 | 1.6 |
| dev | hybrid | 0.8290 | 0.6452 | 0.9220 | 0.6467 | 67.7 | 157.1 |

> This artifact contains development-split comparison only. The locked held-out test result is maintained separately.
