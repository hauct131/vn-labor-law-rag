"""Tests for retrieval, structured generation, and response guardrails."""

from __future__ import annotations

import asyncio
import json

from backend.app.chains.generation_chain import (
    GenerationProviderError,
    GenerationResult,
)
from backend.app.retrieval.models import RetrievalHit
from backend.app.services.rag_service import (
    GENERATION_FAILED_ANSWER,
    INSUFFICIENT_EVIDENCE_ANSWER,
    OUT_OF_SCOPE_ANSWER,
    RAGService,
)


class FakeRetriever:
    def __init__(self, hits: list[RetrievalHit]) -> None:
        self.hits = hits
        self.calls: list[tuple[str, int | None]] = []

    def retrieve(self, query: str, *, top_k: int | None = None):
        self.calls.append((query, top_k))
        return self.hits[:top_k]


class FakeGenerator:
    def __init__(self, payload: dict | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self.payload = payload or {
            "status": "answerable",
            "answer": "Người lao động được nghỉ theo quy định [S1].",
            "cited_source_ids": ["S1"],
        }

    async def ainvoke(self, values):
        self.calls.append(dict(values))
        return GenerationResult(
            answer=json.dumps(self.payload, ensure_ascii=False),
            model="free/test-model",
        )


class FailingGenerator:
    async def ainvoke(self, _values):
        raise GenerationProviderError("provider unavailable", status_code=504)


def make_hit(
    *,
    chunk_id: str = "chunk-113",
    content: str = "Người lao động được nghỉ hằng năm.",
    rank: int = 1,
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        content=content,
        score=3.2,
        rank=rank,
        retrieval_origin="sparse_vncorenlp_bm25",
        payload={
            "article_code": "20.2.LQ.113",
            "article_title": "Nghỉ hằng năm",
            "clause_number": "1",
            "point_labels": ["a"],
            "source_type": "LQ",
            "source_note_text": (
                "(Điều 113 Bộ luật số 45/2019/QH14, có hiệu lực "
                "thi hành kể từ ngày 01/01/2021)"
            ),
            "source_urls": ["https://example.test/dieu-113"],
        },
    )


def test_service_returns_only_sources_declared_and_used_by_model() -> None:
    retriever = FakeRetriever([
        make_hit(chunk_id="chunk-113", rank=1),
        make_hit(chunk_id="chunk-114", rank=2),
    ])
    generator = FakeGenerator({
        "status": "answerable",
        "answer": "Căn cứ được sử dụng nằm tại nguồn thứ hai [S2].",
        "cited_source_ids": ["S2"],
    })
    service = RAGService(
        retriever_provider=lambda _method: retriever,
        generator=generator,
        top_k=5,
    )

    response = asyncio.run(service.ask("  Tôi được nghỉ thế nào?  ", "sparse"))

    assert response.answer.endswith("[S2].")
    assert response.method.value == "sparse"
    assert response.model == "free/test-model"
    assert [source.source_id for source in response.sources] == ["S2"]
    assert response.sources[0].chunk_id == "chunk-114"
    assert retriever.calls == [("Tôi được nghỉ thế nào?", 5)]
    assert "[S1]" in generator.calls[0]["context"]
    assert "[S2]" in generator.calls[0]["context"]
    assert response.total_ms >= response.retrieval_ms


def test_service_skips_llm_when_retrieval_has_no_evidence() -> None:
    retriever = FakeRetriever([])
    generator = FakeGenerator()
    service = RAGService(
        retriever_provider=lambda _method: retriever,
        generator=generator,
    )

    response = asyncio.run(service.ask("Câu hỏi không có nguồn", "hybrid"))

    assert response.answer == INSUFFICIENT_EVIDENCE_ANSWER
    assert response.insufficient_evidence is True
    assert response.generation_ms == 0
    assert response.sources == []
    assert generator.calls == []


def test_service_enforces_out_of_scope_response_and_hides_sources() -> None:
    retriever = FakeRetriever([make_hit()])
    generator = FakeGenerator({
        "status": "out_of_scope",
        "answer": "Câu hỏi thuộc pháp luật đất đai.",
        "cited_source_ids": [],
    })
    service = RAGService(
        retriever_provider=lambda _method: retriever,
        generator=generator,
    )

    response = asyncio.run(service.ask("Tranh chấp đất đai?", "hybrid"))

    assert response.answer == OUT_OF_SCOPE_ANSWER
    assert response.out_of_scope is True
    assert response.insufficient_evidence is True
    assert response.sources == []


def test_service_fails_closed_when_citation_is_invalid() -> None:
    retriever = FakeRetriever([make_hit()])
    generator = FakeGenerator({
        "status": "answerable",
        "answer": "Câu trả lời dùng nguồn không tồn tại [S9].",
        "cited_source_ids": ["S9"],
    })
    service = RAGService(
        retriever_provider=lambda _method: retriever,
        generator=generator,
    )

    response = asyncio.run(service.ask("Câu hỏi", "dense"))

    assert response.answer == INSUFFICIENT_EVIDENCE_ANSWER
    assert response.insufficient_evidence is True
    assert response.sources == []


def test_service_uses_safe_response_when_provider_fails() -> None:
    retriever = FakeRetriever([make_hit()])
    service = RAGService(
        retriever_provider=lambda _method: retriever,
        generator=FailingGenerator(),
    )

    response = asyncio.run(service.ask("Câu hỏi", "hybrid"))

    assert response.answer == GENERATION_FAILED_ANSWER
    assert response.generation_failed is True
    assert response.insufficient_evidence is True
    assert response.sources == []


def test_service_sanitizes_empty_and_duplicate_sources() -> None:
    retriever = FakeRetriever([
        make_hit(chunk_id="duplicate", rank=1),
        make_hit(chunk_id="duplicate", rank=2),
        make_hit(chunk_id="empty", content="   ", rank=3),
    ])
    generator = FakeGenerator()
    service = RAGService(
        retriever_provider=lambda _method: retriever,
        generator=generator,
    )

    response = asyncio.run(service.ask("Câu hỏi", "sparse"))

    assert [source.source_id for source in response.sources] == ["S1"]
    assert [source.chunk_id for source in response.sources] == ["duplicate"]
    assert generator.calls[0]["context"].count("[S1]") == 1
    assert "[S2]" not in generator.calls[0]["context"]


def test_service_rejects_unsupported_method_before_retrieval() -> None:
    def must_not_create_retriever(_method):
        raise AssertionError("retriever must not run for unsupported method")

    service = RAGService(retriever_provider=must_not_create_retriever)

    try:
        asyncio.run(service.ask("Câu hỏi", "graph_enhanced"))
    except ValueError as exc:
        assert "sparse, dense hoặc hybrid" in str(exc)
    else:
        raise AssertionError("unsupported method must be rejected")
