"""HTTP integration tests for saving successful answers to conversation history."""

from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes.health import require_authorized_release
from app.core.config import settings
from app.db.database import Base, get_db_session
from app.main import app
from app.models import conversation as _models  # noqa: F401
from app.schemas.ask import AskResponse, LegalSource
from app.services.rag_service import get_rag_service


class FakeService:
    calls = 0

    async def ask(self, question, method):
        self.calls += 1
        return AskResponse(
            answer=f"Đã trả lời: {question} [S1].",
            method=method,
            sources=[
                LegalSource(
                    source_id="S1",
                    chunk_id="chunk-history",
                    article_code="BLLD2019.113",
                    article_number="113",
                    article_title="Nghỉ hằng năm",
                    document_title="Bộ luật Lao động",
                    document_number="45/2019/QH14",
                    citation_label="Điều 113 Bộ luật Lao động",
                    content="Nội dung điều luật.",
                    score=0.9,
                    rank=1,
                )
            ],
            retrieval_ms=1,
            generation_ms=2,
            total_ms=3,
            model="test-model",
        )


@pytest.fixture
def api_client() -> Generator[tuple[TestClient, FakeService], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_session() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    service = FakeService()
    original_auto_create = settings.database_auto_create
    settings.database_auto_create = False
    app.dependency_overrides[require_authorized_release] = lambda: None
    app.dependency_overrides[get_rag_service] = lambda: service
    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as client:
            yield client, service
    finally:
        app.dependency_overrides.clear()
        settings.database_auto_create = original_auto_create


def test_ask_creates_and_continues_conversation(api_client) -> None:
    client, _service = api_client
    client_id = str(uuid4())
    headers = {"X-Client-Id": client_id}

    first = client.post(
        "/api/ask",
        headers=headers,
        json={"question": "Nghỉ hằng năm bao nhiêu ngày?", "method": "hybrid"},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["history_saved"] is True
    assert first_body["conversation_id"]
    assert first_body["assistant_message_id"]

    conversation_id = first_body["conversation_id"]
    second = client.post(
        "/api/ask",
        headers=headers,
        json={
            "question": "Có được cộng dồn ngày nghỉ không?",
            "method": "sparse",
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id

    detail = client.get(
        f"/api/conversations/{conversation_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [item["role"] for item in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert messages[1]["sources"][0]["article_code"] == "BLLD2019.113"


def test_ask_without_client_id_remains_backward_compatible(api_client) -> None:
    client, _service = api_client
    response = client.post(
        "/api/ask",
        json={"question": "Nghỉ hằng năm bao nhiêu ngày?", "method": "sparse"},
    )
    assert response.status_code == 200
    assert response.json()["history_saved"] is False
    assert response.json()["conversation_id"] is None


def test_invalid_conversation_id_is_rejected_before_llm_call(api_client) -> None:
    client, service = api_client
    response = client.post(
        "/api/ask",
        headers={"X-Client-Id": str(uuid4())},
        json={
            "question": "Nghỉ hằng năm bao nhiêu ngày?",
            "method": "sparse",
            "conversation_id": "not-a-uuid",
        },
    )
    assert response.status_code == 422
    assert service.calls == 0


def test_unknown_conversation_is_rejected_before_llm_call(api_client) -> None:
    client, service = api_client
    response = client.post(
        "/api/ask",
        headers={"X-Client-Id": str(uuid4())},
        json={
            "question": "Nghỉ hằng năm bao nhiêu ngày?",
            "method": "sparse",
            "conversation_id": str(uuid4()),
        },
    )
    assert response.status_code == 404
    assert service.calls == 0


def test_invalid_client_id_is_rejected_before_llm_call(api_client) -> None:
    client, service = api_client
    response = client.post(
        "/api/ask",
        headers={"X-Client-Id": "not-a-uuid"},
        json={"question": "Nghỉ hằng năm bao nhiêu ngày?", "method": "sparse"},
    )
    assert response.status_code == 400
    assert service.calls == 0


def test_other_client_cannot_continue_conversation(api_client) -> None:
    client, service = api_client
    owner_headers = {"X-Client-Id": str(uuid4())}
    created = client.post(
        "/api/ask",
        headers=owner_headers,
        json={"question": "Nghỉ hằng năm bao nhiêu ngày?", "method": "sparse"},
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation_id"]
    calls_after_create = service.calls

    denied = client.post(
        "/api/ask",
        headers={"X-Client-Id": str(uuid4())},
        json={
            "question": "Có được cộng dồn không?",
            "method": "sparse",
            "conversation_id": conversation_id,
        },
    )
    assert denied.status_code == 404
    assert service.calls == calls_after_create


def test_database_failure_does_not_discard_generated_answer(
    api_client,
    tmp_path,
) -> None:
    client, _service = api_client
    broken_engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'missing' / 'application.db'}"
    )
    broken_factory = sessionmaker(
        bind=broken_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def broken_session() -> Generator[Session, None, None]:
        session = broken_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = broken_session
    response = client.post(
        "/api/ask",
        headers={"X-Client-Id": str(uuid4())},
        json={"question": "Nghỉ hằng năm bao nhiêu ngày?", "method": "sparse"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"].startswith("Đã trả lời")
    assert payload["history_saved"] is False
    assert payload["history_error"] == (
        "Câu trả lời đã tạo nhưng chưa lưu được vào lịch sử."
    )


def test_cors_allows_anonymous_client_header(api_client) -> None:
    client, _service = api_client
    response = client.options(
        "/api/conversations",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-client-id",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:5173"
    )
    assert "x-client-id" in response.headers[
        "access-control-allow-headers"
    ].lower()
