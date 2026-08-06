"""Authenticated conversation history and bookmark endpoints."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies.auth import (
    AuthenticatedSession,
    require_authenticated_session,
    require_csrf,
)
from app.db.database import get_db_session
from app.repositories.conversations import (
    ConversationNotFoundError,
    MessageNotFoundError,
    conversation_detail,
    create_conversation,
    delete_conversation,
    list_conversations,
    list_saved_answers,
    remove_bookmark,
    set_bookmark,
    update_conversation_title,
)
from app.schemas.conversation import (
    BookmarkCreate,
    BookmarkResponse,
    ConversationCreate,
    ConversationDetail,
    ConversationListResponse,
    ConversationUpdate,
    SavedAnswerListResponse,
)

router = APIRouter(tags=["Conversation history"])
logger = logging.getLogger(__name__)
DbSession = Annotated[Session, Depends(get_db_session)]
ReadAuth = Annotated[AuthenticatedSession, Depends(require_authenticated_session)]
WriteAuth = Annotated[AuthenticatedSession, Depends(require_csrf)]


def _database_error(exc: SQLAlchemyError) -> HTTPException:
    logger.exception("Conversation database operation failed", exc_info=exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Cơ sở dữ liệu lịch sử hiện không khả dụng.",
    )


@router.get("/conversations", response_model=ConversationListResponse)
def get_conversations(
    session: DbSession,
    authenticated: ReadAuth,
) -> ConversationListResponse:
    try:
        return ConversationListResponse(
            conversations=list_conversations(
                session,
                user_id=authenticated.user.id,
            )
        )
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc


@router.post(
    "/conversations",
    response_model=ConversationDetail,
    status_code=status.HTTP_201_CREATED,
)
def post_conversation(
    payload: ConversationCreate,
    session: DbSession,
    authenticated: WriteAuth,
) -> ConversationDetail:
    try:
        conversation = create_conversation(
            session,
            user_id=authenticated.user.id,
            title=payload.title,
        )
        return conversation_detail(
            session,
            user_id=authenticated.user.id,
            conversation_id=conversation.id,
        )
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
def get_conversation(
    conversation_id: UUID,
    session: DbSession,
    authenticated: ReadAuth,
) -> ConversationDetail:
    try:
        return conversation_detail(
            session,
            user_id=authenticated.user.id,
            conversation_id=str(conversation_id),
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
def patch_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    session: DbSession,
    authenticated: WriteAuth,
) -> ConversationDetail:
    try:
        update_conversation_title(
            session,
            user_id=authenticated.user.id,
            conversation_id=str(conversation_id),
            title=payload.title,
        )
        return conversation_detail(
            session,
            user_id=authenticated.user.id,
            conversation_id=str(conversation_id),
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_conversation(
    conversation_id: UUID,
    session: DbSession,
    authenticated: WriteAuth,
) -> Response:
    try:
        delete_conversation(
            session,
            user_id=authenticated.user.id,
            conversation_id=str(conversation_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc


@router.get("/bookmarks", response_model=SavedAnswerListResponse)
def get_bookmarks(
    session: DbSession,
    authenticated: ReadAuth,
) -> SavedAnswerListResponse:
    try:
        return SavedAnswerListResponse(
            items=list_saved_answers(session, user_id=authenticated.user.id)
        )
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc


@router.put(
    "/bookmarks/{message_id}",
    response_model=BookmarkResponse,
)
def put_bookmark(
    message_id: UUID,
    payload: BookmarkCreate,
    session: DbSession,
    authenticated: WriteAuth,
) -> BookmarkResponse:
    try:
        bookmark = set_bookmark(
            session,
            user_id=authenticated.user.id,
            message_id=str(message_id),
            note=payload.note,
        )
        return BookmarkResponse(
            id=bookmark.id,
            message_id=bookmark.message_id,
            note=bookmark.note,
            created_at=bookmark.created_at,
        )
    except MessageNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy câu trả lời để đánh dấu.",
        ) from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc


@router.delete(
    "/bookmarks/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_bookmark(
    message_id: UUID,
    session: DbSession,
    authenticated: WriteAuth,
) -> Response:
    try:
        remove_bookmark(
            session,
            user_id=authenticated.user.id,
            message_id=str(message_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc
