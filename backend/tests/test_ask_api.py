"""API contract tests with a fake service and zero external calls."""

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.ask import AskResponse, LegalSource, RetrievalMethod
from app.services.rag_service import get_rag_service


class FakeService:
    async def ask(self, question, method):
        if method == RetrievalMethod.GRAPH_ENHANCED:
            raise NotImplementedError("Graph-enhanced đang được hoãn.")
        return AskResponse(
            answer="Câu trả lời có căn cứ [S1].",
            method=method,
            sources=[LegalSource(
                chunk_id="chunk-1",
                article_code="20.2.LQ.1",
                article_number="1",
                article_title="Phạm vi điều chỉnh",
                document_title="Bộ luật Lao động",
                document_number="45/2019/QH14",
                citation_label=(
                    "Điều 1 Bộ luật Lao động số 45/2019/QH14"
                ),
                content="Nội dung nguồn",
                score=1.0,
                rank=1,
                retrieval_origin="sparse_vncorenlp_bm25",
            )],
            retrieval_ms=10.0,
            generation_ms=20.0,
            total_ms=30.0,
            model="free/test-model",
        )


def test_post_ask_returns_frontend_contract() -> None:
    app.dependency_overrides[get_rag_service] = lambda: FakeService()
    try:
        response = TestClient(app).post("/api/ask", json={
            "question": "Bộ luật điều chỉnh vấn đề gì?",
            "method": "sparse",
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["method"] == "sparse"
    assert payload["sources"][0]["article_code"] == "20.2.LQ.1"
    assert payload["sources"][0]["citation_label"] == (
        "Điều 1 Bộ luật Lao động số 45/2019/QH14"
    )
    assert payload["model"] == "free/test-model"


def test_post_ask_rejects_graph_until_graph_stage() -> None:
    app.dependency_overrides[get_rag_service] = lambda: FakeService()
    try:
        response = TestClient(app).post("/api/ask", json={
            "question": "Câu hỏi graph",
            "method": "graph_enhanced",
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 501
    assert "được hoãn" in response.json()["detail"]


def test_post_ask_validates_short_question() -> None:
    response = TestClient(app).post("/api/ask", json={
        "question": "a",
        "method": "sparse",
    })
    assert response.status_code == 422
