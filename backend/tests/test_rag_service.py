"""Tests for retrieval → context → generation orchestration."""

from __future__ import annotations

import asyncio

from backend.app.chains.generation_chain import GenerationResult
from backend.app.retrieval.models import RetrievalHit
from backend.app.services.rag_service import (
    INSUFFICIENT_EVIDENCE_ANSWER,
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
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def ainvoke(self, values):
        self.calls.append(dict(values))
        return GenerationResult(
            answer="Người lao động được nghỉ theo quy định [S1].",
            model="free/test-model",
        )


def make_hit() -> RetrievalHit:
    return RetrievalHit(
        chunk_id="chunk-113",
        content="Người lao động được nghỉ hằng năm.",
        score=3.2,
        rank=1,
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


def test_service_returns_answer_metrics_and_traceable_sources() -> None:
    retriever = FakeRetriever([make_hit()])
    generator = FakeGenerator()
    service = RAGService(
        retriever_provider=lambda _method: retriever,
        generator=generator,
        top_k=5,
    )

    response = asyncio.run(service.ask("  Tôi được nghỉ thế nào?  ", "sparse"))

    assert response.answer.endswith("[S1].")
    assert response.method.value == "sparse"
    assert response.model == "free/test-model"
    assert response.sources[0].article_code == "20.2.LQ.113"
    assert response.sources[0].article_number == "113"
    assert response.sources[0].document_title == "Bộ luật Lao động"
    assert response.sources[0].document_number == "45/2019/QH14"
    assert response.sources[0].citation_label == (
        "Điều 113 Bộ luật Lao động số 45/2019/QH14"
    )
    assert response.sources[0].source_url == "https://example.test/dieu-113"
    assert response.sources[0].point_labels == ["a"]
    assert retriever.calls == [("Tôi được nghỉ thế nào?", 5)]
    assert (
        "Dẫn chứng: Điều 113 Bộ luật Lao động số 45/2019/QH14"
        in generator.calls[0]["context"]
    )
    assert "Mã pháp điển: 20.2.LQ.113" in generator.calls[0]["context"]
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


def test_service_keeps_graph_explicitly_postponed() -> None:
    service = RAGService(retriever_provider=lambda _method: FakeRetriever([]))

    try:
        asyncio.run(service.ask("Câu hỏi", "graph_enhanced"))
    except NotImplementedError as exc:
        assert "được hoãn" in str(exc)
    else:
        raise AssertionError("graph_enhanced must not silently fall back")
