"""API schemas for account and session authentication."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) > 320:
        raise ValueError("Email không được vượt quá 320 ký tự.")
    if normalized.count("@") != 1:
        raise ValueError("Email không hợp lệ.")
    local_part, domain = normalized.split("@", 1)
    if not local_part or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("Email không hợp lệ.")
    if any(char.isspace() for char in normalized):
        raise ValueError("Email không hợp lệ.")
    return normalized


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _normalize_email(value)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Tên hiển thị không được để trống.")
        return normalized


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _normalize_email(value)


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _as_utc(value)


class AuthResponse(BaseModel):
    user: UserResponse
    csrf_token: str
    migrated_anonymous_history: bool = False


class SessionListItem(BaseModel):
    id: str
    current: bool
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime

    @field_validator("created_at", "expires_at", "last_seen_at")
    @classmethod
    def normalize_timestamps(cls, value: datetime) -> datetime:
        return _as_utc(value)


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]
