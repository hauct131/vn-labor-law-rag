"""Repository operations for accounts and server-side login sessions."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.conversation import AppUser, Bookmark, Conversation, UserSession
from app.services.passwords import hash_password, verify_against_dummy, verify_password


class DuplicateEmailError(ValueError):
    """Raised when an account already owns the normalized email."""


class InvalidCredentialsError(ValueError):
    """Raised when an email/password pair cannot be authenticated."""


class InvalidSessionError(ValueError):
    """Raised when a session cookie is absent, expired, revoked, or unknown."""


@dataclass(frozen=True)
class IssuedSession:
    record: UserSession
    token: str
    csrf_token: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(48)


def _email_owner(session: Session, email: str) -> AppUser | None:
    return session.scalar(select(AppUser).where(AppUser.email == email))


def _user_for_update(session: Session, user_id: str | None) -> AppUser | None:
    if user_id is None:
        return None
    return session.scalar(
        select(AppUser)
        .where(AppUser.id == user_id)
        .with_for_update()
    )


def register_user(
    session: Session,
    *,
    email: str,
    password: str,
    display_name: str,
    anonymous_user_id: str | None = None,
) -> tuple[AppUser, bool]:
    if _email_owner(session, email) is not None:
        raise DuplicateEmailError(email)

    migrated = False
    user = _user_for_update(session, anonymous_user_id)
    if user is not None and user.is_anonymous and user.email is None:
        user.email = email
        user.password_hash = hash_password(
            password,
            iterations=settings.password_pbkdf2_iterations,
        )
        user.display_name = display_name
        user.is_anonymous = False
        user.is_active = True
        user.updated_at = _utcnow()
        migrated = True
    else:
        user = AppUser(
            email=email,
            password_hash=hash_password(
                password,
                iterations=settings.password_pbkdf2_iterations,
            ),
            display_name=display_name,
            is_anonymous=False,
            is_active=True,
        )
        session.add(user)

    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateEmailError(email) from exc
    return user, migrated


def authenticate_user(session: Session, *, email: str, password: str) -> AppUser:
    user = _email_owner(session, email)
    if user is None or user.password_hash is None or user.is_anonymous:
        verify_against_dummy(
            password,
            iterations=settings.password_pbkdf2_iterations,
        )
        raise InvalidCredentialsError(email)
    password_matches = verify_password(password, user.password_hash)
    if not user.is_active or not password_matches:
        raise InvalidCredentialsError(email)
    return user


def issue_session(session: Session, *, user: AppUser) -> IssuedSession:
    token = _new_token()
    csrf_token = _new_token()
    now = _utcnow()
    record = UserSession(
        user_id=user.id,
        token_hash=_token_hash(token),
        csrf_token_hash=_token_hash(csrf_token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
    )
    session.add(record)
    session.flush()
    return IssuedSession(record=record, token=token, csrf_token=csrf_token)


def get_session_by_token(
    session: Session,
    *,
    token: str,
    touch: bool = True,
) -> UserSession:
    now = _utcnow()
    record = session.scalar(
        select(UserSession)
        .where(UserSession.token_hash == _token_hash(token))
        .options(selectinload(UserSession.user))
    )
    if (
        record is None
        or record.revoked_at is not None
        or _as_utc(record.expires_at) <= now
        or not record.user.is_active
        or record.user.is_anonymous
    ):
        raise InvalidSessionError()

    touch_interval = timedelta(seconds=settings.session_touch_interval_seconds)
    if touch and now - _as_utc(record.last_seen_at) >= touch_interval:
        record.last_seen_at = now
        session.commit()
    return record


def verify_csrf(record: UserSession, *, cookie_token: str, header_token: str) -> bool:
    if not secrets.compare_digest(
        cookie_token.encode("utf-8"),
        header_token.encode("utf-8"),
    ):
        return False
    return secrets.compare_digest(record.csrf_token_hash, _token_hash(header_token))


def revoke_session(session: Session, *, record: UserSession) -> None:
    if record.revoked_at is None:
        record.revoked_at = _utcnow()
        session.commit()


def revoke_all_sessions(session: Session, *, user_id: str) -> None:
    session.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=_utcnow())
    )
    session.commit()


def list_active_sessions(
    session: Session,
    *,
    user_id: str,
) -> list[UserSession]:
    now = _utcnow()
    return list(
        session.scalars(
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .order_by(UserSession.last_seen_at.desc())
        ).all()
    )


def migrate_anonymous_data(
    session: Session,
    *,
    anonymous_user_id: str,
    account_user_id: str,
) -> bool:
    if anonymous_user_id == account_user_id:
        return False
    anonymous = _user_for_update(session, anonymous_user_id)
    if anonymous is None or not anonymous.is_anonymous:
        return False

    session.execute(
        update(Conversation)
        .where(Conversation.user_id == anonymous_user_id)
        .values(user_id=account_user_id)
    )
    session.execute(
        update(Bookmark)
        .where(Bookmark.user_id == anonymous_user_id)
        .values(user_id=account_user_id)
    )
    session.execute(delete(AppUser).where(AppUser.id == anonymous_user_id))
    session.flush()
    return True
