"""HTTP integration tests for session-owned answer history."""

from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
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
def api_context() -> Generator[
    tuple[TestClient, FakeService, sessionmaker[Session]],
    None,
    None,
]:
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
    original_iterations = settings.password_pbkdf2_iterations
    settings.database_auto_create = False
    settings.password_pbkdf2_iterations = 1000
    app.dependency_overrides[require_authorized_release] = lambda: None
    app.dependency_overrides[get_rag_service] = lambda: service
    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as client:
            yield client, service, factory
    finally:
        app.dependency_overrides.clear()
        settings.database_auto_create = original_auto_create
        settings.password_pbkdf2_iterations = original_iterations
        engine.dispose()


def _register(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "MatKhauAnToan123",
            "display_name": email.split("@", 1)[0],
        },
    )
    assert response.status_code == 201, response.text
    csrf = client.cookies.get(settings.csrf_cookie_name)
    assert csrf
    return {"X-CSRF-Token": csrf}


def test_ask_creates_and_continues_session_owned_conversation(api_context) -> None:
    client, _service, _factory = api_context
    csrf = _register(client, "owner@example.com")

    first = client.post(
        "/api/ask",
        headers=csrf,
        json={"question": "Nghỉ hằng năm bao nhiêu ngày?", "method": "hybrid"},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["history_saved"] is True
    conversation_id = first_body["conversation_id"]
    assert conversation_id

    second = client.post(
        "/api/ask",
        headers=csrf,
        json={
            "question": "Có được cộng dồn ngày nghỉ không?",
            "method": "sparse",
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id

    detail = client.get(f"/api/conversations/{conversation_id}")
    assert detail.status_code == 200
    assert [item["role"] for item in detail.json()["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_public_ask_remains_available_without_session(api_context) -> None:
    client, _service, _factory = api_context
    response = client.post(
        "/api/ask",
        json={"question": "Nghỉ hằng năm bao nhiêu ngày?", "method": "sparse"},
    )
    assert response.status_code == 200
    assert response.json()["history_saved"] is False
    assert response.json()["conversation_id"] is None


def test_authenticated_ask_requires_csrf(api_context) -> None:
    client, service, _factory = api_context
    _register(client, "csrf@example.com")
    calls_before = service.calls
    response = client.post(
        "/api/ask",
        json={"question": "Nghỉ hằng năm bao nhiêu ngày?", "method": "sparse"},
    )
    assert response.status_code == 403
    assert service.calls == calls_before


def test_invalid_and_unknown_conversation_rejected_before_llm(api_context) -> None:
    client, service, _factory = api_context
    csrf = _register(client, "validation@example.com")

    invalid = client.post(
        "/api/ask",
        headers=csrf,
        json={
            "question": "Nghỉ hằng năm bao nhiêu ngày?",
            "method": "sparse",
            "conversation_id": "not-a-uuid",
        },
    )
    assert invalid.status_code == 422
    assert service.calls == 0

    unknown = client.post(
        "/api/ask",
        headers=csrf,
        json={
            "question": "Nghỉ hằng năm bao nhiêu ngày?",
            "method": "sparse",
            "conversation_id": str(uuid4()),
        },
    )
    assert unknown.status_code == 404
    assert service.calls == 0


def test_other_account_cannot_continue_conversation(api_context) -> None:
    owner, service, _factory = api_context
    owner_csrf = _register(owner, "a@example.com")
    created = owner.post(
        "/api/ask",
        headers=owner_csrf,
        json={"question": "Nghỉ hằng năm bao nhiêu ngày?", "method": "sparse"},
    )
    conversation_id = created.json()["conversation_id"]
    calls_after_create = service.calls

    with TestClient(app) as other:
        other_csrf = _register(other, "b@example.com")
        denied = other.post(
            "/api/ask",
            headers=other_csrf,
            json={
                "question": "Có được cộng dồn không?",
                "method": "sparse",
                "conversation_id": conversation_id,
            },
        )
        assert denied.status_code == 404
    assert service.calls == calls_after_create


def test_persistence_failure_does_not_discard_generated_answer(
    api_context,
    monkeypatch,
) -> None:
    client, _service, _factory = api_context
    csrf = _register(client, "failure@example.com")

    def fail_record(*args, **kwargs):
        raise SQLAlchemyError("forced persistence failure")

    monkeypatch.setattr("app.api.routes.ask.record_exchange", fail_record)
    response = client.post(
        "/api/ask",
        headers=csrf,
        json={"question": "Nghỉ hằng năm bao nhiêu ngày?", "method": "sparse"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"].startswith("Đã trả lời")
    assert payload["history_saved"] is False
    assert payload["history_error"] == (
        "Câu trả lời đã tạo nhưng chưa lưu được vào lịch sử."
    )


def test_cors_allows_credentials_and_csrf_header(api_context) -> None:
    client, _service, _factory = api_context
    response = client.options(
        "/api/conversations",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "x-csrf-token" in response.headers["access-control-allow-headers"].lower()


def test_stale_session_cookie_does_not_block_public_ask(api_context) -> None:
    client, _service, _factory = api_context
    csrf = _register(client, "stale@example.com")
    assert client.post("/api/auth/logout", headers=csrf).status_code == 204

    client.cookies.set(settings.session_cookie_name, "stale-session-token")
    response = client.post(
        "/api/ask",
        json={"question": "Nghỉ hằng năm bao nhiêu ngày?", "method": "sparse"},
    )
    assert response.status_code == 200
    assert response.json()["history_saved"] is False
