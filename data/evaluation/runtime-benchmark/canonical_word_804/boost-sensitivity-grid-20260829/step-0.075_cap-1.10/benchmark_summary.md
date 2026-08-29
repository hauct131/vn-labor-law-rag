# Bounded Reranking Ablation

DEV questions: 31; candidate_k=30; context_k=10; RRF=60; dense/sparse=0.9/0.1.

| Metric | Raw RRF@10 | Bounded@10 | Delta |
|---|---:|---:|---:|
| any_article_hit | 1.000000 | 1.000000 | +0.000000 |
| all_article_hit | 0.741935 | 0.741935 | +0.000000 |
| article_recall | 0.872043 | 0.872043 | +0.000000 |
| article_mrr | 0.922043 | 0.922043 | +0.000000 |
| evidence_recall | 0.823028 | 0.823028 | +0.000000 |
| unique_articles | 8.548387 | 8.548387 | +0.000000 |
| unique_documents | 3.419355 | 3.290323 | -0.129032 |
| duplicate_article_rate | 0.145161 | 0.145161 | +0.000000 |
| max_document_concentration | 0.593548 | 0.596774 | +0.003226 |

- Context changed for **12/31** questions.
- Top-5 preservation violations: **0**.
- Article Recall improved/regressed: **0/0**.

Interpretation rule: bounded reranking is considered safe when top-5 preservation violations = 0 and it does not materially degrade article/evidence coverage. Diversity metrics are secondary context-selection diagnostics.
