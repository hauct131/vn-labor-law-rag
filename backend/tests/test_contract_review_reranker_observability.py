from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path

from app.schemas.ask import LegalSource
from app.services.contract_review_reranker import (
    ContractReviewCrossEncoderReranker,
    ContractReviewRerankerConfig,
    RerankedItem,
)
from app.services.contract_review_service import _finalize_reranked_sources


@dataclass
class Source:
    article_code: str
    article_title: str
    source_text: str
    chunk_id: str


class StaticScorer:
    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        assert len(pairs) == len(self.scores)
        return list(self.scores)


class FailingScorer:
    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        raise RuntimeError("synthetic failure")


def cfg(**overrides: object) -> ContractReviewRerankerConfig:
    values = {
        "enabled": True,
        "model_name": "test-model",
        "candidate_k": 15,
        "batch_size": 1,
        "max_length": 512,
        "device": "cpu",
        "normalize_scores": True,
        "fail_open": False,
    }
    values.update(overrides)
    return ContractReviewRerankerConfig(**values)


def _legal_source(*, article: str, chunk: str, lexical_rank: int, score: float) -> LegalSource:
    return LegalSource(
        source_id=f"S{lexical_rank}",
        chunk_id=chunk,
        article_code=article,
        article_number=None,
        article_title=f"Article {article}",
        document_title="Doc",
        document_number="1",
        citation_label=f"Điều {article}",
        clause_number=None,
        point_labels=[],
        content=f"Điều {article} — body",
        score=score,
        rank=lexical_rank,
        retrieval_origin="contract_canonical_lexical_v1",
        source_type="law",
        source_url=None,
        component_ranks={"contract_lexical": lexical_rank},
    )


def test_rerank_with_scores_matches_legacy_selection_exactly() -> None:
    candidates = [
        Source("34", "termination", "wrong subsection", "34-wrong"),
        Source("34", "termination", "expiry subsection", "34-right"),
        Source("35", "employee", "employee termination", "35"),
        Source("36", "employer", "employer termination", "36"),
    ]
    scores = [0.10, 0.98, 0.70, 0.60]

    legacy = ContractReviewCrossEncoderReranker(cfg(), scorer=StaticScorer(scores)).rerank(
        query="Hợp đồng hết hạn", candidates=candidates, top_k=3
    )
    scored = ContractReviewCrossEncoderReranker(cfg(), scorer=StaticScorer(scores)).rerank_with_scores(
        query="Hợp đồng hết hạn", candidates=candidates, top_k=3
    )

    assert [(x.article_code, x.chunk_id) for x in legacy] == [
        (x.source.article_code, x.source.chunk_id) for x in scored
    ]
    assert [x.reranker_score for x in scored] == [0.98, 0.70, 0.60]


def test_rerank_with_scores_fail_open_marks_scores_unavailable() -> None:
    candidates = [
        Source("25", "probation", "first", "a"),
        Source("25", "probation", "duplicate", "b"),
        Source("26", "salary", "second", "c"),
    ]
    reranker = ContractReviewCrossEncoderReranker(
        cfg(fail_open=True), scorer=FailingScorer()
    )

    result = reranker.rerank_with_scores(
        query="thử việc", candidates=candidates, top_k=2
    )

    assert [x.source.chunk_id for x in result] == ["a", "c"]
    assert [x.reranker_score for x in result] == [None, None]


def test_finalize_sources_persists_ce_score_and_final_rank_metadata() -> None:
    a = _legal_source(article="35", chunk="a", lexical_rank=7, score=12.345)
    b = _legal_source(article="36", chunk="b", lexical_rank=2, score=9.876)

    result = _finalize_reranked_sources(
        [
            RerankedItem(original_index=6, reranker_score=0.91234567, source=a),
            RerankedItem(original_index=1, reranker_score=0.70123456, source=b),
        ]
    )

    assert [x.source_id for x in result] == ["S1", "S2"]
    assert [x.rank for x in result] == [1, 2]
    assert [x.score for x in result] == [0.912346, 0.701235]
    assert [x.retrieval_origin for x in result] == [
        "contract_cross_encoder_v2",
        "contract_cross_encoder_v2",
    ]
    assert result[0].component_ranks == {
        "contract_lexical": 7,
        "contract_cross_encoder": 1,
    }
    assert result[1].component_ranks == {
        "contract_lexical": 2,
        "contract_cross_encoder": 2,
    }
    # Input candidates are not mutated.
    assert a.source_id == "S7" and a.score == 12.345 and a.rank == 7
    assert b.source_id == "S2" and b.score == 9.876 and b.rank == 2


def test_finalize_fail_open_keeps_lexical_score_and_origin_but_repairs_rank() -> None:
    source = _legal_source(article="25", chunk="a", lexical_rank=5, score=4.2)
    result = _finalize_reranked_sources(
        [RerankedItem(original_index=4, reranker_score=None, source=source)]
    )

    assert result[0].source_id == "S1"
    assert result[0].rank == 1
    assert result[0].score == 4.2
    assert result[0].retrieval_origin == "contract_canonical_lexical_v1"
    assert result[0].component_ranks == {"contract_lexical": 5}


def test_audit_exporter_uses_canonical_content_without_duplicate_header() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "export_contract_review_source_relevance_audit_v2.py"
    spec = importlib.util.spec_from_file_location("contract_review_audit_v2", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = {
        "article_code": "20.2.LQ.110",
        "article_title": "Nghỉ chuyển ca",
        "content": "Điều 20.2.LQ.110 — Nghỉ chuyển ca\nNguồn: Văn bản hợp nhất\nNội dung.",
    }
    text = module.audit_source_text(source)
    assert text.count("Điều 20.2.LQ.110") == 1
    assert text.count("Nghỉ chuyển ca") == 1
    assert text == source["content"]
