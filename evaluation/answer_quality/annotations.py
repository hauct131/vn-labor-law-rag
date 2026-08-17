"""Schemas for independent model annotations and auditable provider records."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evaluation.answer_quality.schema import (
    AnswerStatus,
    ForbiddenClaim,
    RequiredClaim,
)


PROMPT_VERSION = "answer-golden-annotation-v1"


class AnnotationPayload(BaseModel):
    """Strict content requested independently from every panel model."""

    model_config = ConfigDict(extra="forbid")

    expected_status: AnswerStatus
    required_claims: list[RequiredClaim]
    forbidden_claims: list[ForbiddenClaim]
    review_notes: str = Field(max_length=4000)

    @model_validator(mode="after")
    def validate_status_claims(self) -> Self:
        if self.expected_status == AnswerStatus.ANSWERABLE:
            if not self.required_claims:
                raise ValueError(
                    "answerable annotation requires at least one required claim"
                )
            if any(
                not claim.supported_by_chunk_ids
                for claim in self.required_claims
            ):
                raise ValueError(
                    "every required claim must cite at least one evidence chunk"
                )
        elif self.required_claims:
            raise ValueError(
                "non-answerable annotation must not contain required claims"
            )
        claim_ids = [claim.claim_id for claim in self.required_claims]
        claim_ids.extend(claim.claim_id for claim in self.forbidden_claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("annotation claim ids must be unique")
        return self


class ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)


class AnnotationRecord(BaseModel):
    """One request/response record without authorization headers."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["answer-model-annotation-record-v1"] = (
        "answer-model-annotation-record-v1"
    )
    run_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    resolved_model: str = ""
    response_id: str = ""
    prompt_version: Literal["answer-golden-annotation-v1"] = PROMPT_VERSION
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    finished_at: datetime
    latency_ms: float = Field(ge=0)
    status: Literal["success", "error"]
    raw_content: str = ""
    parsed_annotation: AnnotationPayload | None = None
    usage: ProviderUsage | None = None
    provider_response: dict[str, Any] | None = None
    error: str = ""

    @model_validator(mode="after")
    def validate_result_state(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if self.status == "success":
            if self.parsed_annotation is None or not self.raw_content.strip():
                raise ValueError("successful record requires parsed raw content")
            if not self.resolved_model.strip() or not self.response_id.strip():
                raise ValueError(
                    "successful record requires resolved model and response ID"
                )
            if self.error:
                raise ValueError("successful record must not contain an error")
        else:
            if not self.error.strip():
                raise ValueError("error record requires an error message")
            if self.parsed_annotation is not None:
                raise ValueError("error record must not contain parsed annotation")
        return self


def annotation_response_schema() -> dict[str, Any]:
    """Return the exact JSON Schema sent as OpenRouter response_format."""

    return AnnotationPayload.model_json_schema()
