"""PostgreSQL integration coverage for conversation persistence.

This test is skipped in ordinary local runs. The dedicated CI job supplies a
throw-away database and explicitly allows schema reset.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.integration

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.conversation import Bookmark, Conversation, Message, UserSession
from app.repositories.auth import (
    InvalidSessionError,
    get_session_by_token,
    issue_session,
    register_user,
    revoke_session,
)
from app.repositories.conversations import (
    conversation_detail,
    delete_conversation,
    list_saved_answers,
    record_exchange,
    set_bookmark,
)
from app.schemas.ask import AskResponse, LegalSource, RetrievalMethod


def _database_url() -> str:
    url = os.getenv("TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    if os.getenv("ALLOW_DESTRUCTIVE_POSTGRES_TEST") != "1":
        pytest.skip("destructive PostgreSQL test is not explicitly enabled")
    return url


def _psycopg_url(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _reset_schema_and_apply_migration(database_url: str) -> None:
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    migrations = [
        (migrations_dir / "001_conversations_bookmarks.sql").read_text(encoding="utf-8"),
        (migrations_dir / "002_session_auth.sql").read_text(encoding="utf-8"),
    ]

    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        # The migrations contain ordinary DDL statements. Executing each
        # statement separately avoids driver-specific multi-statement behavior.
        for migration in migrations:
            for statement in migration.split(";"):
                normalized = statement.strip()
                if normalized:
                    connection.execute(normalized)


def _answer() -> AskResponse:
    return AskResponse(
        answer="Người lao động được nghỉ hằng năm theo Điều 113 [S1].",
        method=RetrievalMethod.HYBRID,
        sources=[
            LegalSource(
                source_id="S1",
                chunk_id="chunk-postgres-113",
                article_code="BLLD2019.113",
                article_number="113",
                article_title="Nghỉ hằng năm",
                document_title="Bộ luật Lao động",
                document_number="45/2019/QH14",
                citation_label="Điều 113 Bộ luật Lao động",
                clause_number="1",
                point_labels=["a", "b"],
                content="Người lao động làm đủ 12 tháng được nghỉ hằng năm.",
                score=0.99,
                rank=1,
                retrieval_origin="hybrid",
                source_type="LQ",
                source_url="https://example.test/official",
                component_ranks={"dense": 1, "sparse": 2},
            )
        ],
        retrieval_ms=10,
        generation_ms=20,
        total_ms=30,
        model="integration-model",
    )


def test_postgres_migration_roundtrip_jsonb_and_cascade() -> None:
    database_url = _database_url()
    _reset_schema_and_apply_migration(database_url)

    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    user_id = str(uuid4())

    with factory() as session:
        conversation, _, assistant = record_exchange(
            session,
            user_id=user_id,
            question="Tôi được nghỉ hằng năm bao nhiêu ngày?",
            result=_answer(),
            corpus_release_id="release-postgres-test",
        )
        assistant_id = assistant.id
        conversation_id = conversation.id

    def create_same_bookmark(note: str) -> str:
        with factory() as concurrent_session:
            return set_bookmark(
                concurrent_session,
                user_id=user_id,
                message_id=assistant_id,
                note=note,
            ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        bookmark_ids = list(
            executor.map(create_same_bookmark, ["Ghi chú A", "Ghi chú B"])
        )
    assert len(set(bookmark_ids)) == 1

    with factory() as session:
        detail = conversation_detail(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        assert detail.messages[1].sources[0].point_labels == ["a", "b"]
        assert detail.messages[1].sources[0].component_ranks == {
            "dense": 1,
            "sparse": 2,
        }
        assert detail.messages[1].bookmarked is True

        saved = list_saved_answers(session, user_id=user_id)
        assert len(saved) == 1
        assert saved[0].question == "Tôi được nghỉ hằng năm bao nhiêu ngày?"

        delete_conversation(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        persisted_conversation = session.scalar(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        assert persisted_conversation is None
        assert session.scalars(select(Message)).all() == []
        assert session.scalars(select(Bookmark)).all() == []

    engine.dispose()


def test_postgres_session_auth_roundtrip_and_revocation() -> None:
    database_url = _database_url()
    _reset_schema_and_apply_migration(database_url)

    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    original_iterations = settings.password_pbkdf2_iterations
    settings.password_pbkdf2_iterations = 1000
    try:
        with factory() as session:
            user, migrated = register_user(
                session,
                email="postgres-session@example.test",
                password="PostgresSessionPassword123",
                display_name="Postgres Session",
            )
            assert migrated is False
            issued = issue_session(session, user=user)
            session.commit()
            user_id = user.id
            session_id = issued.record.id
            token = issued.token

        with factory() as session:
            stored = session.get(UserSession, session_id)
            assert stored is not None
            assert stored.user_id == user_id
            assert stored.token_hash != token
            resolved = get_session_by_token(session, token=token, touch=False)
            assert resolved.user.email == "postgres-session@example.test"
            revoke_session(session, record=resolved)

        with factory() as session:
            with pytest.raises(InvalidSessionError):
                get_session_by_token(session, token=token, touch=False)
    finally:
        settings.password_pbkdf2_iterations = original_iterations
        engine.dispose()
