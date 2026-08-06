"""Repository operations for conversation history and bookmarks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.conversation import (
    AppUser,
    Bookmark,
    Conversation,
    Message,
    MessageSource,
)
from app.schemas.ask import AskResponse, LegalSource
from app.schemas.conversation import (
    BookmarkResponse,
    ConversationDetail,
    ConversationSummary,
    SavedAnswer,
    StoredMessage,
    StoredSource,
)


class ConversationNotFoundError(LookupError):
    """Raised when a user tries to access another or missing conversation."""


class MessageNotFoundError(LookupError):
    """Raised when a message is missing or not owned by the user."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_title(value: str | None, fallback: str = "Cuộc hội thoại mới") -> str:
    normalized = " ".join((value or "").split())
    if not normalized:
        return fallback
    return normalized[:160]


def ensure_user(session: Session, user_id: str) -> AppUser:
    user = session.get(AppUser, user_id)
    if user is not None:
        return user

    # Two browser requests may bootstrap the same anonymous user concurrently.
    # A savepoint lets the loser recover from the primary-key race without
    # rolling back the surrounding conversation transaction.
    try:
        with session.begin_nested():
            user = AppUser(id=user_id)
            session.add(user)
            session.flush()
    except IntegrityError:
        user = session.get(AppUser, user_id)
        if user is None:
            raise
    return user


def create_conversation(
    session: Session,
    *,
    user_id: str,
    title: str | None = None,
) -> Conversation:
    ensure_user(session, user_id)
    conversation = Conversation(
        user_id=user_id,
        title=normalize_title(title),
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def get_owned_conversation(
    session: Session,
    *,
    user_id: str,
    conversation_id: str,
    load_messages: bool = False,
) -> Conversation:
    statement = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    )
    if load_messages:
        statement = statement.options(
            selectinload(Conversation.messages).selectinload(Message.sources),
            selectinload(Conversation.messages).selectinload(Message.bookmarks),
        )
    conversation = session.scalar(statement)
    if conversation is None:
        raise ConversationNotFoundError(conversation_id)
    return conversation


def list_conversations(session: Session, *, user_id: str) -> list[ConversationSummary]:
    conversations = session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .options(selectinload(Conversation.messages))
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
    ).all()

    items: list[ConversationSummary] = []
    for conversation in conversations:
        messages = list(conversation.messages)
        last_message = messages[-1] if messages else None
        preview = None
        if last_message is not None:
            compact = " ".join(last_message.content.split())
            preview = compact[:120]
        items.append(
            ConversationSummary(
                id=conversation.id,
                title=conversation.title,
                message_count=len(messages),
                last_message_preview=preview,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
            )
        )
    return items


def _source_schema(source: MessageSource) -> StoredSource:
    return StoredSource(
        id=source.id,
        source_id=source.source_id,
        chunk_id=source.chunk_id,
        article_code=source.article_code,
        article_number=source.article_number,
        article_title=source.article_title,
        document_title=source.document_title,
        document_number=source.document_number,
        citation_label=source.citation_label,
        clause_number=source.clause_number,
        point_labels=list(source.point_labels or []),
        content=source.quoted_text,
        score=source.score,
        rank=source.source_rank,
        retrieval_origin=source.retrieval_origin,
        source_type=source.source_type,
        source_url=source.source_url,
        component_ranks=dict(source.component_ranks or {}),
    )


def _message_schema(message: Message, *, user_id: str) -> StoredMessage:
    bookmark = next(
        (item for item in message.bookmarks if item.user_id == user_id),
        None,
    )
    return StoredMessage(
        id=message.id,
        role=message.role,
        content=message.content,
        corpus_release_id=message.corpus_release_id,
        retrieval_method=message.retrieval_method,
        retrieval_ms=message.retrieval_ms,
        generation_ms=message.generation_ms,
        total_ms=message.total_ms,
        model=message.model,
        insufficient_evidence=message.insufficient_evidence,
        out_of_scope=message.out_of_scope,
        generation_failed=message.generation_failed,
        bookmarked=bookmark is not None,
        bookmark_note=bookmark.note if bookmark is not None else None,
        created_at=message.created_at,
        sources=[_source_schema(source) for source in message.sources],
    )


def conversation_detail(
    session: Session,
    *,
    user_id: str,
    conversation_id: str,
) -> ConversationDetail:
    conversation = get_owned_conversation(
        session,
        user_id=user_id,
        conversation_id=conversation_id,
        load_messages=True,
    )
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            _message_schema(message, user_id=user_id)
            for message in conversation.messages
        ],
    )


def update_conversation_title(
    session: Session,
    *,
    user_id: str,
    conversation_id: str,
    title: str,
) -> Conversation:
    conversation = get_owned_conversation(
        session,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    conversation.title = normalize_title(title)
    conversation.updated_at = _utcnow()
    session.commit()
    session.refresh(conversation)
    return conversation


def delete_conversation(
    session: Session,
    *,
    user_id: str,
    conversation_id: str,
) -> None:
    conversation = get_owned_conversation(
        session,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    session.delete(conversation)
    session.commit()


def _message_source(source: LegalSource) -> MessageSource:
    return MessageSource(
        source_id=source.source_id,
        chunk_id=source.chunk_id,
        article_code=source.article_code,
        article_number=source.article_number,
        article_title=source.article_title,
        document_title=source.document_title,
        document_number=source.document_number,
        citation_label=source.citation_label,
        clause_number=source.clause_number,
        point_labels=list(source.point_labels),
        quoted_text=source.content,
        score=source.score,
        source_rank=source.rank,
        retrieval_origin=source.retrieval_origin,
        source_type=source.source_type,
        source_url=source.source_url,
        component_ranks=dict(source.component_ranks),
    )


def record_exchange(
    session: Session,
    *,
    user_id: str,
    question: str,
    result: AskResponse,
    corpus_release_id: str,
    conversation_id: str | None = None,
) -> tuple[Conversation, Message, Message]:
    ensure_user(session, user_id)
    if conversation_id is None:
        conversation = Conversation(
            user_id=user_id,
            title=normalize_title(question),
        )
        session.add(conversation)
        session.flush()
    else:
        conversation = get_owned_conversation(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    recorded_at = _utcnow()
    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=question,
        created_at=recorded_at,
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=result.answer,
        created_at=recorded_at + timedelta(microseconds=1),
        corpus_release_id=corpus_release_id,
        retrieval_method=result.method.value,
        retrieval_ms=result.retrieval_ms,
        generation_ms=result.generation_ms,
        total_ms=result.total_ms,
        model=result.model,
        insufficient_evidence=result.insufficient_evidence,
        out_of_scope=result.out_of_scope,
        generation_failed=result.generation_failed,
        sources=[_message_source(source) for source in result.sources],
    )
    conversation.updated_at = _utcnow()
    session.add_all([user_message, assistant_message])
    session.commit()
    session.refresh(conversation)
    session.refresh(user_message)
    session.refresh(assistant_message)
    return conversation, user_message, assistant_message


def _owned_assistant_message(
    session: Session,
    *,
    user_id: str,
    message_id: str,
) -> Message:
    message = session.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.id == message_id,
            Message.role == "assistant",
            Conversation.user_id == user_id,
        )
        .options(
            selectinload(Message.sources),
            selectinload(Message.bookmarks),
            selectinload(Message.conversation),
        )
    )
    if message is None:
        raise MessageNotFoundError(message_id)
    return message


def set_bookmark(
    session: Session,
    *,
    user_id: str,
    message_id: str,
    note: str | None,
) -> Bookmark:
    ensure_user(session, user_id)
    _owned_assistant_message(session, user_id=user_id, message_id=message_id)
    bookmark = session.scalar(
        select(Bookmark).where(
            Bookmark.user_id == user_id,
            Bookmark.message_id == message_id,
        )
    )
    normalized_note = (note or "").strip() or None
    if bookmark is None:
        try:
            with session.begin_nested():
                bookmark = Bookmark(
                    user_id=user_id,
                    message_id=message_id,
                    note=normalized_note,
                )
                session.add(bookmark)
                session.flush()
        except IntegrityError:
            # PUT is idempotent even when two tabs bookmark the same answer at
            # nearly the same time. Re-read the row created by the other request.
            bookmark = session.scalar(
                select(Bookmark).where(
                    Bookmark.user_id == user_id,
                    Bookmark.message_id == message_id,
                )
            )
            if bookmark is None:
                raise
            bookmark.note = normalized_note
    else:
        bookmark.note = normalized_note
    session.commit()
    session.refresh(bookmark)
    return bookmark


def remove_bookmark(
    session: Session,
    *,
    user_id: str,
    message_id: str,
) -> bool:
    bookmark = session.scalar(
        select(Bookmark).where(
            Bookmark.user_id == user_id,
            Bookmark.message_id == message_id,
        )
    )
    if bookmark is None:
        return False
    session.delete(bookmark)
    session.commit()
    return True


def _previous_user_question(messages: Iterable[Message], target: Message) -> str | None:
    previous: str | None = None
    for message in messages:
        if message.id == target.id:
            return previous
        if message.role == "user":
            previous = message.content
    return previous


def list_saved_answers(session: Session, *, user_id: str) -> list[SavedAnswer]:
    bookmarks = session.scalars(
        select(Bookmark)
        .where(Bookmark.user_id == user_id)
        .options(
            selectinload(Bookmark.message).selectinload(Message.sources),
            selectinload(Bookmark.message).selectinload(Message.bookmarks),
            selectinload(Bookmark.message)
            .selectinload(Message.conversation)
            .selectinload(Conversation.messages),
        )
        .order_by(Bookmark.created_at.desc())
    ).all()

    items: list[SavedAnswer] = []
    for bookmark in bookmarks:
        message = bookmark.message
        conversation = message.conversation
        items.append(
            SavedAnswer(
                bookmark=BookmarkResponse(
                    id=bookmark.id,
                    message_id=message.id,
                    note=bookmark.note,
                    created_at=bookmark.created_at,
                ),
                conversation_id=conversation.id,
                conversation_title=conversation.title,
                question=_previous_user_question(conversation.messages, message),
                answer=_message_schema(message, user_id=user_id),
            )
        )
    return items
