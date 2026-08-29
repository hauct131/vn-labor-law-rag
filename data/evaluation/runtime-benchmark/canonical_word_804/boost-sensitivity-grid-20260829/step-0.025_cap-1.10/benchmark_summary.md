# Bounded Reranking Ablation

DEV questions: 31; candidate_k=30; context_k=10; RRF=60; dense/sparse=0.9/0.1.

| Metric | Raw RRF@10 | Bounded@10 | Delta |
|---|---:|---:|---:|
| any_article_hit | 1.000000 | 1.000000 | +0.000000 |
| all_article_hit | 0.741935 | 0.741935 | +0.000000 |
| article_recall | 0.872043 | 0.878495 | +0.006452 |
| article_mrr | 0.922043 | 0.922043 | +0.000000 |
| evidence_recall | 0.823028 | 0.836392 | +0.013364 |
| unique_articles | 8.548387 | 8.451613 | -0.096774 |
| unique_documents | 3.419355 | 3.096774 | -0.322581 |
| duplicate_article_rate | 0.145161 | 0.154839 | +0.009678 |
| max_document_concentration | 0.593548 | 0.625806 | +0.032258 |

- Context changed for **25/31** questions.
- Top-5 preservation violations: **0**.
- Article Recall improved/regressed: **1/0**.

Interpretation rule: bounded reranking is considered safe when top-5 preservation violations = 0 and it does not materially degrade article/evidence coverage. Diversity metrics are secondary context-selection diagnostics.
