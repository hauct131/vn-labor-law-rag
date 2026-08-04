# Production-equivalent retrieval benchmark

The benchmark uses the locked runtime split. Evidence metrics are reported in JSON but are not used for model selection.

| Split | Method | Article Recall@10 | All-Article Hit@10 | Article MRR@10 | Multi-Article Recall@10 | p50 ms | p95 ms |
|---|---|---:|---:|---:|---:|---:|---:|
| dev | dense | 0.8815 | 0.7419 | 0.8911 | 0.7550 | 74.7 | 126.9 |
| dev | sparse | 0.8180 | 0.6129 | 0.7941 | 0.6239 | 1.6 | 2.8 |
| dev | hybrid | 0.8839 | 0.7419 | 0.9274 | 0.7600 | 85.7 | 150.5 |
| test | dense | 0.9744 | 0.9231 | 0.8538 | 0.9333 | 77.7 | 147.8 |
| test | sparse | 0.9231 | 0.9231 | 0.7628 | 0.8000 | 2.4 | 3.5 |
| test | hybrid | 0.9744 | 0.9231 | 0.8038 | 0.9333 | 71.2 | 112.7 |
