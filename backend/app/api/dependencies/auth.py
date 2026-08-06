"""FastAPI dependencies for authenticated server-side sessions."""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db_session
from app.models.conversation import AppUser, UserSession
from app.repositories.auth import (
    InvalidSessionError,
    get_session_by_token,
    verify_csrf,
)


class AuthenticatedSession:
    def __init__(self, record: UserSession) -> None:
        self.record = record
        self.user: AppUser = record.user


DbSession = Annotated[Session, Depends(get_db_session)]


def optional_authenticated_session(
    session: DbSession,
    session_token: Annotated[
        str | None,
        Cookie(alias=settings.session_cookie_name),
    ] = None,
) -> AuthenticatedSession | None:
    if session_token is None:
        return None
    try:
        return AuthenticatedSession(
            get_session_by_token(session, token=session_token)
        )
    except InvalidSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn.",
        ) from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cơ sở dữ liệu đăng nhập hiện không khả dụng.",
        ) from exc


def require_authenticated_session(
    authenticated: Annotated[
        AuthenticatedSession | None,
        Depends(optional_authenticated_session),
    ],
) -> AuthenticatedSession:
    if authenticated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cần đăng nhập để sử dụng chức năng này.",
        )
    return authenticated


def require_authenticated_user(
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(require_authenticated_session),
    ],
) -> AppUser:
    return authenticated.user


def require_csrf(
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(require_authenticated_session),
    ],
    csrf_cookie: Annotated[
        str | None,
        Cookie(alias=settings.csrf_cookie_name),
    ] = None,
    csrf_header: Annotated[
        str | None,
        Header(alias="X-CSRF-Token"),
    ] = None,
) -> AuthenticatedSession:
    if (
        not csrf_cookie
        or not csrf_header
        or not verify_csrf(
            authenticated.record,
            cookie_token=csrf_cookie,
            header_token=csrf_header,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token không hợp lệ.",
        )
    return authenticated


def optional_write_authenticated_session(
    session: DbSession,
    session_token: Annotated[
        str | None,
        Cookie(alias=settings.session_cookie_name),
    ] = None,
    csrf_cookie: Annotated[
        str | None,
        Cookie(alias=settings.csrf_cookie_name),
    ] = None,
    csrf_header: Annotated[
        str | None,
        Header(alias="X-CSRF-Token"),
    ] = None,
) -> AuthenticatedSession | None:
    """Allow public writes, but require CSRF whenever a valid session is used."""

    if session_token is None:
        return None
    try:
        authenticated = AuthenticatedSession(
            get_session_by_token(session, token=session_token)
        )
    except InvalidSessionError:
        # A stale cookie must not block public Q&A after a session expires.
        return None
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cơ sở dữ liệu đăng nhập hiện không khả dụng.",
        ) from exc

    if (
        not csrf_cookie
        or not csrf_header
        or not verify_csrf(
            authenticated.record,
            cookie_token=csrf_cookie,
            header_token=csrf_header,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token không hợp lệ.",
        )
    return authenticated
