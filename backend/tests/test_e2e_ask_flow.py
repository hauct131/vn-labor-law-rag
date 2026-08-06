"""In-process HTTP → real RAGService → response contract smoke test."""

import json

from fastapi.testclient import TestClient

from app.api.routes.health import require_authorized_release
from app.chains.generation_chain import GenerationResult
from app.main import app
from app.retrieval.models import RetrievalHit
from app.services.rag_service import RAGService, get_rag_service


class OneHitRetriever:
    def retrieve(self, query: str, *, top_k: int | None = None):
        assert query == "Nghỉ hằng năm bao nhiêu ngày?"
        assert top_k == 5
        return [RetrievalHit(
            chunk_id="chunk-e2e",
            content="Người lao động có số ngày nghỉ hằng năm theo Điều 113.",
            score=4.25,
            rank=1,
            retrieval_origin="sparse_vncorenlp_bm25",
            payload={
                "article_code": "20.2.LQ.113",
                "article_title": "Nghỉ hằng năm",
                "source_type": "LQ",
                "source_note_text": (
                    "(Điều 113 Bộ luật số 45/2019/QH14, có hiệu lực "
                    "thi hành kể từ ngày 01/01/2021)"
                ),
                "source_urls": ["https://example.test/dieu-113"],
            },
        )]


class GroundedGenerator:
    async def ainvoke(self, values):
        assert "[S1]" in values["context"]
        assert "20.2.LQ.113" in values["context"]
        assert (
            "Điều 113 Bộ luật Lao động số 45/2019/QH14"
            in values["context"]
        )
        return GenerationResult(
            answer=json.dumps(
                {
                    "status": "answerable",
                    "answer": "Quy định nằm tại Điều 113 [S1].",
                    "cited_source_ids": ["S1"],
                },
                ensure_ascii=False,
            ),
            model="free/e2e-model",
        )


def test_http_request_runs_real_orchestration_end_to_end() -> None:
    service = RAGService(
        retriever_provider=lambda _method: OneHitRetriever(),
        generator=GroundedGenerator(),
        top_k=5,
    )
    app.dependency_overrides[require_authorized_release] = lambda: None
    app.dependency_overrides[get_rag_service] = lambda: service
    try:
        response = TestClient(app).post("/api/ask", json={
            "question": "Nghỉ hằng năm bao nhiêu ngày?",
            "method": "sparse",
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "Quy định nằm tại Điều 113 [S1]."
    assert payload["sources"][0]["article_code"] == "20.2.LQ.113"
    assert payload["sources"][0]["citation_label"] == (
        "Điều 113 Bộ luật Lao động số 45/2019/QH14"
    )
    assert payload["sources"][0]["source_url"].endswith("dieu-113")
    assert payload["model"] == "free/e2e-model"
    assert payload["total_ms"] >= payload["retrieval_ms"]
