"""API schemas for persisted conversations and bookmarks."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=160)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=160)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Tên hội thoại không được để trống.")
        return normalized


class BookmarkCreate(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class StoredSource(BaseModel):
    id: str
    source_id: str | None = None
    chunk_id: str
    article_code: str | None = None
    article_number: str | None = None
    article_title: str | None = None
    document_title: str | None = None
    document_number: str | None = None
    citation_label: str | None = None
    clause_number: str | None = None
    point_labels: list[str] = Field(default_factory=list)
    content: str
    score: float | None = None
    rank: int
    retrieval_origin: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    component_ranks: dict[str, int] = Field(default_factory=dict)


class StoredMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    corpus_release_id: str | None = None
    retrieval_method: str | None = None
    retrieval_ms: float | None = None
    generation_ms: float | None = None
    total_ms: float | None = None
    model: str | None = None
    insufficient_evidence: bool = False
    out_of_scope: bool = False
    generation_failed: bool = False
    bookmarked: bool = False
    bookmark_note: str | None = None
    created_at: datetime
    sources: list[StoredSource] = Field(default_factory=list)

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _as_utc(value)


class ConversationSummary(BaseModel):
    id: str
    title: str
    message_count: int
    last_message_preview: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamps(cls, value: datetime) -> datetime:
        return _as_utc(value)


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[StoredMessage]

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamps(cls, value: datetime) -> datetime:
        return _as_utc(value)


class BookmarkResponse(BaseModel):
    id: str
    message_id: str
    note: str | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _as_utc(value)


class SavedAnswer(BaseModel):
    bookmark: BookmarkResponse
    conversation_id: str
    conversation_title: str
    question: str | None = None
    answer: StoredMessage


class SavedAnswerListResponse(BaseModel):
    items: list[SavedAnswer]
