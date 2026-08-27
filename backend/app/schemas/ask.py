from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class RetrievalMethod(StrEnum):
    SPARSE = "sparse"
    DENSE = "dense"
    HYBRID = "hybrid"


class FallbackReason(StrEnum):
    RETRIEVAL_CONTEXT_EMPTY = "retrieval_context_empty"
    SCOPE_CLASSIFIER_OUT_OF_SCOPE = "scope_classifier_out_of_scope"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_HTTP_429 = "provider_http_429"
    PROVIDER_HTTP_5XX = "provider_http_5xx"
    PROVIDER_HTTP_4XX = "provider_http_4xx"
    PROVIDER_EMPTY_CONTENT = "provider_empty_content"
    PROVIDER_FINISH_REASON_LENGTH = "provider_finish_reason_length"
    PROVIDER_INVALID_RESPONSE = "provider_invalid_response"
    GENERATION_PARSE_ERROR = "generation_parse_error"
    CITATION_PARSE_ERROR = "citation_parse_error"
    CITATION_GUARDRAIL_REJECTED = "citation_guardrail_rejected"
    INSUFFICIENT_SUPPORTED_CLAIMS = "insufficient_supported_claims"
    UNKNOWN = "unknown"



class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    method: RetrievalMethod
    conversation_id: UUID | None = None


class LegalSource(BaseModel):
    source_id: str | None = None
    chunk_id: str
    article_code: str | None = None
    article_number: str | None = None
    article_title: str | None = None
    document_title: str | None = None
    document_number: str | None = None
    citation_label: str
    clause_number: str | None = None
    point_labels: list[str] = Field(default_factory=list)
    content: str
    score: float | None = None
    rank: int
    retrieval_origin: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    component_ranks: dict[str, int] = Field(default_factory=dict)


class AskResponse(BaseModel):
    answer: str
    method: RetrievalMethod
    sources: list[LegalSource]
    retrieval_ms: float
    generation_ms: float
    total_ms: float
    model: str | None = None
    insufficient_evidence: bool = False
    out_of_scope: bool = False
    generation_failed: bool = False
    history_saved: bool = False
    history_error: str | None = None
    conversation_id: str | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    fallback_reason: str | None = None
    attempt: int = 1
    attempt_records: list[dict[str, Any]] = Field(default_factory=list)
