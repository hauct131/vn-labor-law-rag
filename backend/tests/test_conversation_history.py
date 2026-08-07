"""Repository and authenticated HTTP tests for history and bookmarks."""

from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes.auth import router as auth_router
from app.api.routes.conversations import router as conversation_router
from app.core.config import settings
from app.db.database import (
    Base,
    get_db_session,
    get_engine,
    get_session_factory,
    initialize_database,
)
from app.models import conversation as _models  # noqa: F401
from app.repositories.conversations import record_exchange
from app.schemas.ask import AskResponse, LegalSource, RetrievalMethod


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    app = FastAPI()
    app.include_router(auth_router, prefix="/api")
    app.include_router(conversation_router, prefix="/api")

    def override_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    original_iterations = settings.password_pbkdf2_iterations
    settings.password_pbkdf2_iterations = 1000
    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        settings.password_pbkdf2_iterations = original_iterations


def _register(client: TestClient) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"{uuid4()}@example.com",
            "password": "MatKhauAnToan123",
            "display_name": "Người thử nghiệm",
        },
    )
    assert response.status_code == 201
    csrf = client.cookies.get(settings.csrf_cookie_name)
    assert csrf
    return response.json()["user"]["id"], {"X-CSRF-Token": csrf}


def _answer() -> AskResponse:
    return AskResponse(
        answer="Người lao động được nghỉ hằng năm theo Điều 113.",
        method=RetrievalMethod.HYBRID,
        sources=[
            LegalSource(
                source_id="S1",
                chunk_id="chunk-113-1",
                article_code="BLLD2019.113",
                article_number="113",
                article_title="Nghỉ hằng năm",
                document_title="Bộ luật Lao động",
                document_number="45/2019/QH14",
                citation_label="Điều 113 Bộ luật Lao động",
                content="Người lao động làm đủ 12 tháng được nghỉ hằng năm.",
                score=0.91,
                rank=1,
            )
        ],
        retrieval_ms=12.5,
        generation_ms=30.0,
        total_ms=42.5,
        model="test-model",
    )


def test_database_initialization_creates_expected_tables(tmp_path) -> None:
    original_url = settings.database_url
    database_path = tmp_path / "application.db"
    settings.database_url = f"sqlite+pysqlite:///{database_path}"
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    try:
        initialize_database()
        tables = set(inspect(get_engine()).get_table_names())
        assert {
            "app_users",
            "user_sessions",
            "conversations",
            "messages",
            "message_sources",
            "bookmarks",
        }.issubset(tables)
    finally:
        get_engine().dispose()
        get_session_factory.cache_clear()
        get_engine.cache_clear()
        settings.database_url = original_url


def test_conversation_crud_bookmark_and_saved_answer(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    user_id, csrf = _register(client)
    created = client.post(
        "/api/conversations",
        headers=csrf,
        json={"title": "  Nghỉ   hằng năm  "},
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    assert created.json()["title"] == "Nghỉ hằng năm"

    with session_factory() as session:
        _conversation, user_message, assistant = record_exchange(
            session,
            user_id=user_id,
            question="Tôi được nghỉ hằng năm bao nhiêu ngày?",
            result=_answer(),
            corpus_release_id="release-test",
            conversation_id=conversation_id,
        )
        user_message_id = user_message.id
        assistant_id = assistant.id

    assert client.put(
        f"/api/bookmarks/{user_message_id}",
        headers=csrf,
        json={},
    ).status_code == 404
    saved = client.put(
        f"/api/bookmarks/{assistant_id}",
        headers=csrf,
        json={"note": "Dùng cho báo cáo"},
    )
    assert saved.status_code == 200
    updated = client.put(
        f"/api/bookmarks/{assistant_id}",
        headers=csrf,
        json={"note": "Ghi chú mới"},
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == saved.json()["id"]

    detail = client.get(f"/api/conversations/{conversation_id}")
    assert detail.status_code == 200
    assert [item["role"] for item in detail.json()["messages"]] == [
        "user",
        "assistant",
    ]
    assert detail.json()["messages"][1]["bookmarked"] is True
    assert detail.json()["messages"][1]["sources"][0]["article_code"] == "BLLD2019.113"

    bookmarks = client.get("/api/bookmarks")
    assert bookmarks.status_code == 200
    assert bookmarks.json()["items"][0]["question"].startswith("Tôi được nghỉ")

    renamed = client.patch(
        f"/api/conversations/{conversation_id}",
        headers=csrf,
        json={"title": "Tuổi nghỉ hưu"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Tuổi nghỉ hưu"

    assert client.delete(
        f"/api/conversations/{conversation_id}",
        headers=csrf,
    ).status_code == 204
    assert client.get("/api/bookmarks").json()["items"] == []


def test_history_endpoints_require_login(client: TestClient) -> None:
    assert client.get("/api/conversations").status_code == 401
    assert client.get("/api/bookmarks").status_code == 401
