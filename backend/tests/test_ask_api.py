"""API contract tests with a fake service and zero external calls."""

from fastapi.testclient import TestClient

from app.api.routes.health import (
    release_gate_dependency,
    require_authorized_release,
)
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


def allow_release() -> None:
    return None


def test_post_ask_returns_frontend_contract() -> None:
    app.dependency_overrides[require_authorized_release] = allow_release
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


def test_post_ask_accepts_dense_method() -> None:
    app.dependency_overrides[require_authorized_release] = allow_release
    app.dependency_overrides[get_rag_service] = lambda: FakeService()
    try:
        response = TestClient(app).post("/api/ask", json={
            "question": "Quy định về nghỉ hằng năm?",
            "method": "dense",
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["method"] == "dense"


def test_post_ask_rejects_graph_until_graph_stage() -> None:
    app.dependency_overrides[require_authorized_release] = allow_release
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
    app.dependency_overrides[require_authorized_release] = allow_release
    try:
        response = TestClient(app).post("/api/ask", json={
            "question": "a",
            "method": "sparse",
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_post_ask_blocks_unapproved_release_before_service_call() -> None:
    class MustNotRunService:
        async def ask(self, question, method):
            raise AssertionError("service must not run behind a closed gate")

    app.dependency_overrides[release_gate_dependency] = lambda: {
        "status": "not_ready",
        "release_id": "candidate",
        "release_status": "pending",
        "chunk_count": 833,
        "chunk_sha256": "a" * 64,
        "errors": ["authority_review_pending"],
    }
    app.dependency_overrides[get_rag_service] = lambda: MustNotRunService()
    try:
        response = TestClient(app).post("/api/ask", json={
            "question": "Điều kiện hưởng lương là gì?",
            "method": "sparse",
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "release_not_ready",
        "release_id": "candidate",
        "errors": ["authority_review_pending"],
    }
