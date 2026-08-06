"""Account registration, login, logout, and session inspection endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies.auth import (
    AuthenticatedSession,
    require_authenticated_session,
    require_csrf,
)
from app.core.config import settings
from app.db.database import get_db_session
from app.models.conversation import AppUser
from app.repositories.auth import (
    DuplicateEmailError,
    InvalidCredentialsError,
    authenticate_user,
    issue_session,
    list_active_sessions,
    migrate_anonymous_data,
    register_user,
    revoke_all_sessions,
    revoke_session,
    verify_csrf,
)
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    RegisterRequest,
    SessionListItem,
    SessionListResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
DbSession = Annotated[Session, Depends(get_db_session)]


def _database_unavailable(session: Session, exc: SQLAlchemyError) -> HTTPException:
    session.rollback()
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Cơ sở dữ liệu đăng nhập hiện không khả dụng.",
    )


def _optional_client_id(
    x_client_id: Annotated[str | None, Header(alias="X-Client-Id")] = None,
) -> str | None:
    if x_client_id is None:
        return None
    try:
        return str(UUID(x_client_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Client-Id phải là UUID hợp lệ.",
        ) from exc


def _user_response(user: AppUser) -> UserResponse:
    if user.email is None or user.display_name is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tài khoản chưa có đủ thông tin đăng nhập.",
        )
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
    )


def _set_auth_cookies(response: Response, *, token: str, csrf_token: str) -> None:
    common = {
        "secure": settings.session_cookie_secure,
        "samesite": settings.session_cookie_samesite,
        "path": "/",
        "max_age": settings.session_ttl_seconds,
    }
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        **common,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        **common,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegisterRequest,
    response: Response,
    session: DbSession,
    anonymous_user_id: Annotated[str | None, Depends(_optional_client_id)],
) -> AuthResponse:
    try:
        user, migrated = register_user(
            session,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            anonymous_user_id=anonymous_user_id,
        )
        issued = issue_session(session, user=user)
        session.commit()
        session.refresh(user)
        session.refresh(issued.record)
    except DuplicateEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email đã được đăng ký.",
        ) from exc
    except SQLAlchemyError as exc:
        raise _database_unavailable(session, exc) from exc

    _set_auth_cookies(response, token=issued.token, csrf_token=issued.csrf_token)
    return AuthResponse(
        user=_user_response(user),
        csrf_token=issued.csrf_token,
        migrated_anonymous_history=migrated,
    )


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    response: Response,
    session: DbSession,
    anonymous_user_id: Annotated[str | None, Depends(_optional_client_id)],
) -> AuthResponse:
    try:
        user = authenticate_user(
            session,
            email=payload.email,
            password=payload.password,
        )
        migrated = False
        if anonymous_user_id:
            migrated = migrate_anonymous_data(
                session,
                anonymous_user_id=anonymous_user_id,
                account_user_id=user.id,
            )
        issued = issue_session(session, user=user)
        session.commit()
        session.refresh(user)
        session.refresh(issued.record)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email hoặc mật khẩu không đúng.",
        ) from exc
    except SQLAlchemyError as exc:
        raise _database_unavailable(session, exc) from exc

    _set_auth_cookies(response, token=issued.token, csrf_token=issued.csrf_token)
    return AuthResponse(
        user=_user_response(user),
        csrf_token=issued.csrf_token,
        migrated_anonymous_history=migrated,
    )


@router.get("/me", response_model=AuthResponse)
def me(
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(require_authenticated_session),
    ],
    csrf_cookie: Annotated[
        str | None,
        Cookie(alias=settings.csrf_cookie_name),
    ] = None,
) -> AuthResponse:
    if not csrf_cookie or not verify_csrf(
        authenticated.record,
        cookie_token=csrf_cookie,
        header_token=csrf_cookie,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên đăng nhập thiếu CSRF token hợp lệ.",
        )
    return AuthResponse(
        user=_user_response(authenticated.user),
        csrf_token=csrf_cookie,
    )


@router.get("/sessions", response_model=SessionListResponse)
def sessions(
    session: DbSession,
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(require_authenticated_session),
    ],
) -> SessionListResponse:
    try:
        current_id = authenticated.record.id
        records = list_active_sessions(session, user_id=authenticated.user.id)
        return SessionListResponse(
            sessions=[
                SessionListItem(
                    id=record.id,
                    current=record.id == current_id,
                    created_at=record.created_at,
                    expires_at=record.expires_at,
                    last_seen_at=record.last_seen_at,
                )
                for record in records
            ]
        )
    except SQLAlchemyError as exc:
        raise _database_unavailable(session, exc) from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session: DbSession,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf)],
) -> Response:
    try:
        revoke_session(session, record=authenticated.record)
    except SQLAlchemyError as exc:
        raise _database_unavailable(session, exc) from exc
    _clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(
    response: Response,
    session: DbSession,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf)],
) -> Response:
    try:
        revoke_all_sessions(session, user_id=authenticated.user.id)
    except SQLAlchemyError as exc:
        raise _database_unavailable(session, exc) from exc
    _clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
