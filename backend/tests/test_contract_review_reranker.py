from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.contract_review_reranker import (
    ContractReviewCrossEncoderReranker,
    ContractReviewRerankerConfig,
    default_source_text,
)


@dataclass
class Source:
    article_code: str
    article_title: str
    source_text: str
    chunk_id: str


class StaticScorer:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.seen_pairs: list[tuple[str, str]] = []

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        self.seen_pairs = list(pairs)
        return list(self.scores)


class FailingScorer:
    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        raise RuntimeError("synthetic failure")


def cfg(**overrides: object) -> ContractReviewRerankerConfig:
    values = {
        "enabled": True,
        "model_name": "test-model",
        "candidate_k": 20,
        "batch_size": 4,
        "max_length": 512,
        "device": "cpu",
        "normalize_scores": True,
        "fail_open": True,
    }
    values.update(overrides)
    return ContractReviewRerankerConfig(**values)


def test_reranker_orders_candidates_by_cross_encoder_score() -> None:
    candidates = [
        Source("35", "NLĐ đơn phương", "employee termination", "a"),
        Source("36", "NSDLĐ đơn phương", "employer termination", "b"),
        Source("NĐ7", "Công việc đặc thù", "special occupations", "c"),
    ]
    scorer = StaticScorer([0.95, 0.20, 0.10])
    reranker = ContractReviewCrossEncoderReranker(cfg(), scorer=scorer)

    result = reranker.rerank(
        query="Người lao động muốn đơn phương chấm dứt hợp đồng",
        candidates=candidates,
        top_k=2,
    )

    assert [item.article_code for item in result] == ["35", "36"]
    assert scorer.seen_pairs[0][0].startswith("Người lao động")
    assert "employee termination" in scorer.seen_pairs[0][1]


def test_best_scoring_chunk_wins_before_article_deduplication() -> None:
    candidates = [
        Source("34", "Chấm dứt HĐLĐ", "wrong subsection", "wrong"),
        Source("34", "Chấm dứt HĐLĐ", "expiry subsection", "right"),
        Source("35", "NLĐ đơn phương", "other article", "other"),
    ]
    scorer = StaticScorer([0.10, 0.98, 0.40])
    reranker = ContractReviewCrossEncoderReranker(cfg(), scorer=scorer)

    result = reranker.rerank(query="Hợp đồng hết hạn", candidates=candidates, top_k=2)

    assert [(item.article_code, item.chunk_id) for item in result] == [
        ("34", "right"),
        ("35", "other"),
    ]


def test_fail_open_preserves_lexical_order_and_dedupes_articles() -> None:
    candidates = [
        Source("25", "Thời gian thử việc", "first", "a"),
        Source("25", "Thời gian thử việc", "duplicate", "b"),
        Source("26", "Lương thử việc", "second article", "c"),
    ]
    reranker = ContractReviewCrossEncoderReranker(cfg(), scorer=FailingScorer())

    result = reranker.rerank(query="thử việc", candidates=candidates, top_k=2)

    assert [item.chunk_id for item in result] == ["a", "c"]


def test_fail_closed_propagates_reranker_error() -> None:
    reranker = ContractReviewCrossEncoderReranker(
        cfg(fail_open=False), scorer=FailingScorer()
    )
    with pytest.raises(RuntimeError, match="synthetic failure"):
        reranker.rerank(
            query="test",
            candidates=[Source("1", "A", "B", "x")],
            top_k=1,
        )


def test_disabled_reranker_is_exact_baseline_selector() -> None:
    candidates = [
        Source("1", "A", "first", "a"),
        Source("2", "B", "second", "b"),
        Source("3", "C", "third", "c"),
    ]
    scorer = StaticScorer([0.0, 0.0, 1.0])
    reranker = ContractReviewCrossEncoderReranker(
        cfg(enabled=False), scorer=scorer
    )

    result = reranker.rerank(query="query", candidates=candidates, top_k=2)

    assert [item.article_code for item in result] == ["1", "2"]
    assert scorer.seen_pairs == []


def test_mapping_sources_are_supported_without_project_model_dependency() -> None:
    source = {
        "article_code": "20.2.LQ.102",
        "article_title": "Khấu trừ tiền lương",
        "source_text": "Mức khấu trừ không được quá 30%.",
    }
    text = default_source_text(source)
    assert "20.2.LQ.102" in text
    assert "Khấu trừ tiền lương" in text
    assert "30%" in text


def test_config_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTRACT_REVIEW_RERANKER_ENABLED", "true")
    monkeypatch.setenv("CONTRACT_REVIEW_RERANKER_CANDIDATE_K", "24")
    monkeypatch.setenv("CONTRACT_REVIEW_RERANKER_BATCH_SIZE", "2")
    monkeypatch.setenv("CONTRACT_REVIEW_RERANKER_DEVICE", "cpu")

    config = ContractReviewRerankerConfig.from_env()

    assert config.enabled is True
    assert config.candidate_k == 24
    assert config.batch_size == 2
    assert config.device == "cpu"
