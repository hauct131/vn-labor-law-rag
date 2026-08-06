from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes.conversations import router
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
    app.include_router(router, prefix="/api")

    def override_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as test_client:
        yield test_client


def _headers(client_id: str) -> dict[str, str]:
    return {"X-Client-Id": client_id}


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
                clause_number="1",
                point_labels=[],
                content="Người lao động làm đủ 12 tháng được nghỉ hằng năm.",
                score=0.91,
                rank=1,
                retrieval_origin="hybrid",
                source_type="LQ",
                source_url="https://example.test/official",
                component_ranks={"dense": 1, "sparse": 2},
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


def test_conversation_crud_and_ownership(client: TestClient) -> None:
    user_a = str(uuid4())
    user_b = str(uuid4())

    created = client.post(
        "/api/conversations",
        headers=_headers(user_a),
        json={"title": "  Nghỉ   hằng năm  "},
    )
    assert created.status_code == 201
    conversation = created.json()
    assert conversation["title"] == "Nghỉ hằng năm"
    conversation_id = conversation["id"]

    listed = client.get("/api/conversations", headers=_headers(user_a))
    assert listed.status_code == 200
    assert listed.json()["conversations"][0]["id"] == conversation_id

    denied = client.get(
        f"/api/conversations/{conversation_id}",
        headers=_headers(user_b),
    )
    assert denied.status_code == 404

    renamed = client.patch(
        f"/api/conversations/{conversation_id}",
        headers=_headers(user_a),
        json={"title": "Tuổi nghỉ hưu"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Tuổi nghỉ hưu"

    blank_title = client.patch(
        f"/api/conversations/{conversation_id}",
        headers=_headers(user_a),
        json={"title": "   "},
    )
    assert blank_title.status_code == 422

    deleted = client.delete(
        f"/api/conversations/{conversation_id}",
        headers=_headers(user_a),
    )
    assert deleted.status_code == 204
    assert client.get(
        f"/api/conversations/{conversation_id}",
        headers=_headers(user_a),
    ).status_code == 404


def test_invalid_or_missing_client_id_is_rejected(client: TestClient) -> None:
    assert client.get("/api/conversations").status_code == 422
    response = client.get(
        "/api/conversations",
        headers={"X-Client-Id": "not-a-uuid"},
    )
    assert response.status_code == 400
    assert "UUID" in response.json()["detail"]


def test_record_exchange_bookmark_and_saved_answer(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    user_id = str(uuid4())
    with session_factory() as session:
        conversation, user_message, assistant_message = record_exchange(
            session,
            user_id=user_id,
            question="Tôi được nghỉ hằng năm bao nhiêu ngày?",
            result=_answer(),
            corpus_release_id="release-test",
        )
        conversation_id = conversation.id
        user_message_id = user_message.id
        assistant_message_id = assistant_message.id
        assert user_message.role == "user"
        assert assistant_message.sources[0].article_code == "BLLD2019.113"

    detail = client.get(
        f"/api/conversations/{conversation_id}",
        headers=_headers(user_id),
    )
    assert detail.status_code == 200
    body = detail.json()
    assert [message["role"] for message in body["messages"]] == [
        "user",
        "assistant",
    ]
    assert body["messages"][1]["corpus_release_id"] == "release-test"
    assert body["created_at"].endswith("Z")
    assert body["messages"][0]["created_at"].endswith("Z")
    assert body["messages"][1]["sources"][0]["content"].startswith(
        "Người lao động"
    )

    user_message_bookmark = client.put(
        f"/api/bookmarks/{user_message_id}",
        headers=_headers(user_id),
        json={},
    )
    assert user_message_bookmark.status_code == 404

    saved = client.put(
        f"/api/bookmarks/{assistant_message_id}",
        headers=_headers(user_id),
        json={"note": "Dùng cho báo cáo"},
    )
    assert saved.status_code == 200
    assert saved.json()["note"] == "Dùng cho báo cáo"

    # PUT is idempotent and updates the existing bookmark instead of duplicating it.
    updated = client.put(
        f"/api/bookmarks/{assistant_message_id}",
        headers=_headers(user_id),
        json={"note": "Ghi chú mới"},
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == saved.json()["id"]

    bookmarks = client.get("/api/bookmarks", headers=_headers(user_id))
    assert bookmarks.status_code == 200
    items = bookmarks.json()["items"]
    assert len(items) == 1
    assert items[0]["question"] == "Tôi được nghỉ hằng năm bao nhiêu ngày?"
    assert items[0]["answer"]["bookmarked"] is True
    assert items[0]["answer"]["sources"][0]["article_code"] == "BLLD2019.113"

    other_user = str(uuid4())
    assert client.put(
        f"/api/bookmarks/{assistant_message_id}",
        headers=_headers(other_user),
        json={},
    ).status_code == 404
    # DELETE is idempotent and must not reveal or remove another user's bookmark.
    assert client.delete(
        f"/api/bookmarks/{assistant_message_id}",
        headers=_headers(other_user),
    ).status_code == 204
    assert len(client.get("/api/bookmarks", headers=_headers(user_id)).json()["items"]) == 1

    removed = client.delete(
        f"/api/bookmarks/{assistant_message_id}",
        headers=_headers(user_id),
    )
    assert removed.status_code == 204
    assert client.get("/api/bookmarks", headers=_headers(user_id)).json()["items"] == []


def test_deleting_conversation_cascades_to_saved_answer(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    user_id = str(uuid4())
    with session_factory() as session:
        conversation, _, assistant_message = record_exchange(
            session,
            user_id=user_id,
            question="Tuổi nghỉ hưu là bao nhiêu?",
            result=_answer(),
            corpus_release_id="release-test",
        )
        conversation_id = conversation.id
        assistant_message_id = assistant_message.id

    assert client.put(
        f"/api/bookmarks/{assistant_message_id}",
        headers=_headers(user_id),
        json={},
    ).status_code == 200
    assert client.delete(
        f"/api/conversations/{conversation_id}",
        headers=_headers(user_id),
    ).status_code == 204
    assert client.get("/api/bookmarks", headers=_headers(user_id)).json()["items"] == []
