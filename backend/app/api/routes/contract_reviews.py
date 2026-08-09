"""Authenticated labor contract review endpoints."""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies.auth import AuthenticatedSession, require_authenticated_session, require_csrf
from app.db.database import get_db_session
from app.repositories.contract_reviews import (
    ContractReviewNotFoundError,
    create_contract_review,
    delete_contract_review,
    get_contract_review,
    list_contract_reviews,
)
from app.schemas.ask import RetrievalMethod
from app.schemas.contract_review import ContractReviewListResponse, ContractReviewResponse
from app.services.contract_file_extraction import ContractFileError, extract_contract
from app.services.contract_review_service import review_contract
from app.api.routes.health import require_authorized_corpus


router = APIRouter(prefix="/contract-reviews", tags=["Contract reviews"])
logger = logging.getLogger(__name__)
DbSession = Annotated[Session, Depends(get_db_session)]
ReadAuth = Annotated[AuthenticatedSession, Depends(require_authenticated_session)]
WriteAuth = Annotated[AuthenticatedSession, Depends(require_csrf)]


def _database_error(exc: SQLAlchemyError) -> HTTPException:
    logger.exception("Contract review database operation failed", exc_info=exc)
    return HTTPException(status_code=503, detail="Cơ sở dữ liệu rà soát hiện không khả dụng.")


@router.post("", response_model=ContractReviewResponse, status_code=status.HTTP_201_CREATED)
async def post_contract_review(
    session: DbSession,
    authenticated: WriteAuth,
    file: Annotated[UploadFile, File(...)],
    method: Annotated[RetrievalMethod, Form()] = RetrievalMethod.SPARSE,
    _release_gate: None = Depends(require_authorized_corpus),
) -> ContractReviewResponse:
    if method != RetrievalMethod.SPARSE:
        raise HTTPException(
            status_code=422,
            detail="Rà soát hợp đồng v1 chỉ hỗ trợ truy hồi căn cứ canonical theo từ khóa.",
        )
    data = await file.read(10 * 1024 * 1024 + 1)
    try:
        extracted = extract_contract(file.filename, file.content_type, data)
        draft = review_contract(extracted.text, method)
        record = create_contract_review(
            session,
            user_id=authenticated.user.id,
            filename=extracted.filename,
            file_sha256=hashlib.sha256(data).hexdigest(),
            mime_type=extracted.mime_type,
            file_size_bytes=len(data),
            method=method,
            extracted_character_count=len(extracted.text),
            draft=draft,
        )
        return get_contract_review(session, user_id=authenticated.user.id, review_id=record.id)
    except ContractFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("Contract review evidence retrieval failed", exc_info=exc)
        raise HTTPException(status_code=503, detail="Không thể truy hồi căn cứ pháp luật cho hợp đồng.") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc


@router.get("", response_model=ContractReviewListResponse)
def get_contract_reviews(
    session: DbSession,
    authenticated: ReadAuth,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ContractReviewListResponse:
    try:
        items, total = list_contract_reviews(
            session, user_id=authenticated.user.id, limit=limit, offset=offset
        )
        return ContractReviewListResponse(items=items, total=total, limit=limit, offset=offset)
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc


@router.get("/{review_id}", response_model=ContractReviewResponse)
def get_contract_review_detail(
    review_id: UUID,
    session: DbSession,
    authenticated: ReadAuth,
) -> ContractReviewResponse:
    try:
        return get_contract_review(session, user_id=authenticated.user.id, review_id=str(review_id))
    except ContractReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo rà soát.") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc


@router.delete("/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_contract_review(
    review_id: UUID,
    session: DbSession,
    authenticated: WriteAuth,
) -> Response:
    try:
        delete_contract_review(session, user_id=authenticated.user.id, review_id=str(review_id))
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ContractReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo rà soát.") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise _database_error(exc) from exc
