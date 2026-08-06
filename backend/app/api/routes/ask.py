"""Public question-answering endpoint."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...chains.generation_chain import (
    GenerationConfigurationError,
    GenerationProviderError,
)
from ...core.config import settings
from ...db.database import get_db_session
from ...repositories.conversations import (
    ConversationNotFoundError,
    get_owned_conversation,
    record_exchange,
)
from ...retrieval.models import (
    RetrievalBackendError,
    RetrievalConfigurationError,
)
from ...schemas.ask import AskRequest, AskResponse
from ...services.rag_service import RAGService, get_rag_service
from .conversations import optional_client_id
from .health import require_authorized_release


router = APIRouter(tags=["Question answering"])
logger = logging.getLogger(__name__)


@router.post("/ask", response_model=AskResponse)
async def ask_question(
    payload: AskRequest,
    _release_gate: None = Depends(require_authorized_release),
    service: RAGService = Depends(get_rag_service),
    client_id: Annotated[str | None, Depends(optional_client_id)] = None,
    session: Session = Depends(get_db_session),
) -> AskResponse:
    if payload.conversation_id and client_id is None:
        raise HTTPException(
            status_code=400,
            detail="Cần X-Client-Id khi tiếp tục một hội thoại.",
        )

    if payload.conversation_id and client_id:
        try:
            get_owned_conversation(
                session,
                user_id=client_id,
                conversation_id=str(payload.conversation_id),
            )
        except ConversationNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="Không tìm thấy hội thoại.",
            ) from exc
        except SQLAlchemyError as exc:
            session.rollback()
            logger.exception("Failed to validate conversation ownership", exc_info=exc)
            raise HTTPException(
                status_code=503,
                detail="Cơ sở dữ liệu lịch sử hiện không khả dụng.",
            ) from exc

    try:
        result = await service.ask(payload.question, payload.method)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except GenerationConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GenerationProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RetrievalConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RetrievalBackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Không thể khởi tạo bộ truy hồi: {exc}",
        ) from exc

    if client_id is None:
        return result

    try:
        conversation, user_message, assistant_message = record_exchange(
            session,
            user_id=client_id,
            question=payload.question,
            result=result,
            corpus_release_id=settings.corpus_release_id,
            conversation_id=(
                str(payload.conversation_id) if payload.conversation_id else None
            ),
        )
    except (SQLAlchemyError, ConversationNotFoundError) as exc:
        session.rollback()
        logger.exception("Failed to persist conversation history", exc_info=exc)
        return result.model_copy(
            update={
                "history_saved": False,
                "history_error": (
                    "Câu trả lời đã tạo nhưng chưa lưu được vào lịch sử."
                ),
            }
        )

    return result.model_copy(
        update={
            "history_saved": True,
            "conversation_id": conversation.id,
            "user_message_id": user_message.id,
            "assistant_message_id": assistant_message.id,
        }
    )
