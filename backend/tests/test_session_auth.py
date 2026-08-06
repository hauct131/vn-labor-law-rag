"""Authentication, CSRF, session lifecycle, and cross-user isolation tests."""

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes.auth import router as auth_router
from app.api.routes.conversations import router as conversation_router
from app.core.config import settings
from app.db.database import Base, get_db_session
from app.models import conversation as _models  # noqa: F401
from app.models.conversation import AppUser, UserSession
from app.repositories.conversations import record_exchange
from app.schemas.ask import AskResponse, LegalSource, RetrievalMethod


@pytest.fixture
def app_and_factory() -> Generator[
    tuple[FastAPI, sessionmaker[Session]],
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
    app = FastAPI()
    app.include_router(auth_router, prefix="/api")
    app.include_router(conversation_router, prefix="/api")

    def override_session() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    original_iterations = settings.password_pbkdf2_iterations
    settings.password_pbkdf2_iterations = 1000
    app.dependency_overrides[get_db_session] = override_session
    try:
        yield app, factory
    finally:
        settings.password_pbkdf2_iterations = original_iterations
        engine.dispose()


def _register(
    client: TestClient,
    *,
    email: str,
    name: str,
    client_id: str | None = None,
) -> tuple[dict[str, object], dict[str, str]]:
    headers = {"X-Client-Id": client_id} if client_id else {}
    response = client.post(
        "/api/auth/register",
        headers=headers,
        json={
            "email": email,
            "password": "MatKhauAnToan123",
            "display_name": name,
        },
    )
    assert response.status_code == 201, response.text
    csrf = client.cookies.get(settings.csrf_cookie_name)
    assert csrf
    return response.json(), {"X-CSRF-Token": csrf}


def _answer() -> AskResponse:
    return AskResponse(
        answer="Người lao động được nghỉ hằng năm theo Điều 113.",
        method=RetrievalMethod.HYBRID,
        sources=[
            LegalSource(
                source_id="S1",
                chunk_id="chunk-113-1",
                article_code="BLLD2019.113",
                citation_label="Điều 113 Bộ luật Lao động",
                content="Nội dung điều luật.",
                score=0.91,
                rank=1,
            )
        ],
        retrieval_ms=1,
        generation_ms=2,
        total_ms=3,
        model="test-model",
    )


def test_register_promotes_anonymous_user_and_hashes_secrets(app_and_factory) -> None:
    app, factory = app_and_factory
    anonymous_id = str(uuid4())
    with factory() as session:
        conversation, _, _ = record_exchange(
            session,
            user_id=anonymous_id,
            question="Lịch sử cũ",
            result=_answer(),
            corpus_release_id="release-test",
        )
        conversation_id = conversation.id

    with TestClient(app) as client:
        body, _csrf = _register(
            client,
            email="USERA@example.com",
            name="Nguyễn Văn A",
            client_id=anonymous_id,
        )
        assert body["migrated_anonymous_history"] is True
        assert body["user"]["id"] == anonymous_id
        assert client.get("/api/auth/me").status_code == 200
        assert client.get(f"/api/conversations/{conversation_id}").status_code == 200

        set_cookie = ", ".join(client.post(
            "/api/auth/login",
            json={"email": "usera@example.com", "password": "MatKhauAnToan123"},
        ).headers.get_list("set-cookie")).lower()
        assert "httponly" in set_cookie
        assert "samesite=lax" in set_cookie

    with factory() as session:
        user = session.get(AppUser, anonymous_id)
        assert user is not None
        assert user.email == "usera@example.com"
        assert user.password_hash != "MatKhauAnToan123"
        assert user.password_hash and user.password_hash.startswith("pbkdf2_sha256$")
        stored = session.scalar(select(UserSession))
        assert stored is not None
        assert len(stored.token_hash) == 64
        assert settings.session_cookie_name not in stored.token_hash


def test_duplicate_email_wrong_password_and_expired_session(app_and_factory) -> None:
    app, factory = app_and_factory
    with TestClient(app) as owner:
        _register(owner, email="a@example.com", name="A")

    with TestClient(app) as other:
        duplicate = other.post(
            "/api/auth/register",
            json={
                "email": "A@example.com",
                "password": "MatKhauKhac123",
                "display_name": "Khác",
            },
        )
        assert duplicate.status_code == 409
        wrong = other.post(
            "/api/auth/login",
            json={"email": "a@example.com", "password": "sai-mat-khau"},
        )
        assert wrong.status_code == 401

        logged_in = other.post(
            "/api/auth/login",
            json={"email": "a@example.com", "password": "MatKhauAnToan123"},
        )
        assert logged_in.status_code == 200
        token = other.cookies.get(settings.session_cookie_name)
        assert token

        with factory() as session:
            record = session.scalar(select(UserSession).order_by(UserSession.created_at.desc()))
            assert record is not None
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
        assert other.get("/api/auth/me").status_code == 401


def test_csrf_logout_and_cross_user_isolation(app_and_factory) -> None:
    app, factory = app_and_factory
    with TestClient(app) as user_a, TestClient(app) as user_b:
        body_a, csrf_a = _register(user_a, email="a@example.com", name="User A")
        _body_b, csrf_b = _register(user_b, email="b@example.com", name="User B")
        user_a_id = str(body_a["user"]["id"])

        missing_csrf = user_a.post("/api/conversations", json={"title": "A"})
        assert missing_csrf.status_code == 403

        created = user_a.post(
            "/api/conversations",
            headers=csrf_a,
            json={"title": "Hội thoại của A"},
        )
        assert created.status_code == 201
        conversation_id = created.json()["id"]
        assert user_b.get(f"/api/conversations/{conversation_id}").status_code == 404

        with factory() as session:
            _conversation, _, assistant = record_exchange(
                session,
                user_id=user_a_id,
                question="Câu hỏi của A",
                result=_answer(),
                corpus_release_id="release-test",
                conversation_id=conversation_id,
            )
            assistant_id = assistant.id

        denied_bookmark = user_b.put(
            f"/api/bookmarks/{assistant_id}",
            headers=csrf_b,
            json={},
        )
        assert denied_bookmark.status_code == 404
        assert user_a.put(
            f"/api/bookmarks/{assistant_id}",
            headers=csrf_a,
            json={},
        ).status_code == 200

        assert user_a.post("/api/auth/logout").status_code == 403
        assert user_a.post("/api/auth/logout", headers=csrf_a).status_code == 204
        assert user_a.get("/api/auth/me").status_code == 401
        assert user_b.get("/api/auth/me").status_code == 200


def test_existing_anonymous_schema_is_upgraded_in_place(tmp_path) -> None:
    database_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE app_users ("
            "id VARCHAR(36) PRIMARY KEY, "
            "created_at DATETIME NOT NULL"
            ")"
        )
        connection.exec_driver_sql(
            "INSERT INTO app_users(id, created_at) VALUES (?, CURRENT_TIMESTAMP)",
            (str(uuid4()),),
        )

    from app.db.migrations import apply_session_auth_compatibility

    apply_session_auth_compatibility(engine)
    Base.metadata.create_all(engine)
    columns = {item["name"] for item in inspect(engine).get_columns("app_users")}
    assert {
        "email",
        "password_hash",
        "display_name",
        "is_active",
        "is_anonymous",
        "updated_at",
    }.issubset(columns)
    assert "user_sessions" in inspect(engine).get_table_names()
    engine.dispose()


def test_login_migrates_anonymous_history_into_existing_account(app_and_factory) -> None:
    app, factory = app_and_factory
    anonymous_id = str(uuid4())
    with TestClient(app) as account_client:
        body, _csrf = _register(
            account_client,
            email="existing@example.com",
            name="Existing User",
        )
        account_id = str(body["user"]["id"])
        account_client.cookies.clear()

    with factory() as session:
        conversation, _, _ = record_exchange(
            session,
            user_id=anonymous_id,
            question="Lịch sử trước đăng nhập",
            result=_answer(),
            corpus_release_id="release-test",
        )
        conversation_id = conversation.id

    with TestClient(app) as browser:
        login = browser.post(
            "/api/auth/login",
            headers={"X-Client-Id": anonymous_id},
            json={
                "email": "existing@example.com",
                "password": "MatKhauAnToan123",
            },
        )
        assert login.status_code == 200, login.text
        assert login.json()["migrated_anonymous_history"] is True
        assert login.json()["user"]["id"] == account_id
        assert browser.get(f"/api/conversations/{conversation_id}").status_code == 200

    with factory() as session:
        assert session.get(AppUser, anonymous_id) is None


def test_logout_all_revokes_every_active_session(app_and_factory) -> None:
    app, _factory = app_and_factory
    with TestClient(app) as first, TestClient(app) as second:
        _body, first_csrf = _register(
            first,
            email="sessions@example.com",
            name="Sessions User",
        )
        login = second.post(
            "/api/auth/login",
            json={
                "email": "sessions@example.com",
                "password": "MatKhauAnToan123",
            },
        )
        assert login.status_code == 200
        second_csrf = second.cookies.get(settings.csrf_cookie_name)
        assert second_csrf

        sessions = first.get("/api/auth/sessions")
        assert sessions.status_code == 200
        assert len(sessions.json()["sessions"]) == 2
        assert sum(item["current"] for item in sessions.json()["sessions"]) == 1

        logout_all = first.post("/api/auth/logout-all", headers=first_csrf)
        assert logout_all.status_code == 204
        assert first.get("/api/auth/me").status_code == 401
        assert second.get("/api/auth/me").status_code == 401
        assert second.post(
            "/api/auth/logout",
            headers={"X-CSRF-Token": second_csrf},
        ).status_code == 401


def test_registration_rolls_back_if_session_cannot_be_created(
    app_and_factory,
    monkeypatch,
) -> None:
    app, factory = app_and_factory

    def fail_issue_session(*args, **kwargs):
        raise SQLAlchemyError("forced session failure")

    monkeypatch.setattr("app.api.routes.auth.issue_session", fail_issue_session)
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/register",
            json={
                "email": "rollback@example.com",
                "password": "MatKhauAnToan123",
                "display_name": "Rollback",
            },
        )
        assert response.status_code == 503

    with factory() as session:
        stored = session.scalar(
            select(AppUser).where(AppUser.email == "rollback@example.com")
        )
        assert stored is None
