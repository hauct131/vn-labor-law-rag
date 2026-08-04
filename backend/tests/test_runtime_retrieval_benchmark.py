from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.retrieval.models import RetrievalHit
from scripts.benchmark_runtime_retrieval import (
    BenchmarkError,
    QueryRun,
    _comparison,
    load_locked_split,
    metric_values,
    summarize_runs,
)


def make_hit(chunk_id: str, article_code: str, rank: int) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        content=f"Nội dung {chunk_id}",
        score=1.0 / rank,
        rank=rank,
        retrieval_origin="test",
        payload={"chunk_id": chunk_id, "article_code": article_code},
    )


def question(question_id: str = "q1") -> dict:
    return {
        "id": question_id,
        "question": "Câu hỏi",
        "category": "multi_article",
        "expected_article_codes": ["A", "B"],
        "evidence_chunk_ids": ["a", "b"],
    }


def test_article_metrics_and_latency_summary_are_complete():
    q = question()
    hits = [make_hit("a", "A", 1), make_hit("x", "X", 2), make_hit("b", "B", 3)]
    values = metric_values(q, hits, 3)
    assert values["all_article_hit"] == 1.0
    assert values["article_recall"] == 1.0
    assert values["article_mrr"] == 1.0
    assert values["all_evidence_hit"] == 1.0

    summary = summarize_runs(
        [QueryRun(q, hits, 10.0), QueryRun(question("q2"), hits[:1], 20.0)],
        [1, 10],
    )
    assert summary["latency_ms"]["p50"] == 15.0
    assert summary["latency_ms"]["p95"] == 19.5
    assert summary["stratified_at_10"]["multi_article"]["article_recall"] == 0.75


def test_dense_hybrid_comparison_counts_improvement_and_regression():
    q1 = question("q1")
    q2 = question("q2")
    dense = [
        QueryRun(q1, [make_hit("a", "A", 1)], 1.0),
        QueryRun(q2, [make_hit("a", "A", 1), make_hit("b", "B", 2)], 1.0),
    ]
    hybrid = [
        QueryRun(q1, [make_hit("a", "A", 1), make_hit("b", "B", 2)], 1.0),
        QueryRun(q2, [make_hit("x", "X", 1)], 1.0),
    ]
    comparison = _comparison(dense, hybrid)
    assert comparison["improved_count"] == 1
    assert comparison["regressed_count"] == 1


def test_locked_split_loader_rejects_candidate_dataset():
    with pytest.raises(BenchmarkError, match="locked"):
        load_locked_split(
            SimpleNamespace(
                is_file=lambda: True,
                read_text=lambda **_: '{"locked": false, "questions": []}',
            ),
            "dev",
        )
