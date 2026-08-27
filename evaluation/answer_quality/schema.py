"""Strict schema for answer and citation evaluation datasets.

Candidate labels may be assisted by language models, but a dataset cannot be
marked locked until every item has been human-adjudicated. This prevents a
multi-model consensus from being represented as legal authority approval.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

class DatasetStatus(StrEnum):
    DRAFT_CANDIDATE = "draft_candidate"
    MULTI_LLM_REVIEWED = "multi_llm_reviewed"
    HUMAN_ADJUDICATED = "human_adjudicated"
    LOCKED = "locked"


class LabelStatus(StrEnum):
    PENDING_HUMAN_EXTRACTION = "pending_human_extraction"
    MULTI_LLM_CANDIDATE = "multi_llm_candidate"
    NEEDS_HUMAN_ADJUDICATION = "needs_human_adjudication"
    HUMAN_ADJUDICATED = "human_adjudicated"


class AuthorityReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AnswerStatus(StrEnum):
    ANSWERABLE = "answerable"
    OUT_OF_SCOPE = "out_of_scope"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


def _unique_non_blank(values: list[str], field_name: str) -> list[str]:
    normalized = [str(value).strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError(f"{field_name} must not contain blank values")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


def _non_blank(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


class RequiredClaim(BaseModel):
    """One atomic statement that a complete answer must contain."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    text: str = Field(min_length=1, max_length=2000)
    supported_by_article_codes: list[str] = Field(min_length=1)
    supported_by_chunk_ids: list[str] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _non_blank(value, "text")

    @field_validator("supported_by_article_codes", "supported_by_chunk_ids")
    @classmethod
    def normalize_references(
        cls,
        value: list[str],
        info: Any,
    ) -> list[str]:
        return _unique_non_blank(value, info.field_name)


class ForbiddenClaim(BaseModel):
    """A material falsehood or unsupported assertion that must not appear."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    text: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("text", "reason")
    @classmethod
    def normalize_text_fields(cls, value: str, info: Any) -> str:
        return _non_blank(value, info.field_name)


class AnswerQualityQuestion(BaseModel):
    """Ground-truth candidate for one answer-quality evaluation case."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    question: str = Field(min_length=3, max_length=2000)
    category: str = Field(min_length=1, max_length=100)
    question_type: str = Field(min_length=1, max_length=100)
    difficulty: str = Field(pattern=r"^(easy|medium|hard)$")
    document_number: str = Field(default="", max_length=200)
    document_title: str = Field(default="", max_length=1000)
    retrieval_label_origin: str = Field(default="", max_length=200)
    expected_status: AnswerStatus
    expected_article_codes: list[str] = Field(default_factory=list)
    expected_article_ids: list[str] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    evidence_excerpt: str = Field(default="", max_length=30000)
    required_claims: list[RequiredClaim] = Field(default_factory=list)
    forbidden_claims: list[ForbiddenClaim] = Field(default_factory=list)
    label_status: LabelStatus = LabelStatus.PENDING_HUMAN_EXTRACTION
    authority_review_status: AuthorityReviewStatus = (
        AuthorityReviewStatus.PENDING
    )
    reviewer: str = Field(default="", max_length=200)
    review_notes: str = Field(default="", max_length=4000)
    benchmark_enabled: bool = False

    @field_validator(
        "expected_article_codes",
        "expected_article_ids",
        "evidence_chunk_ids",
    )
    @classmethod
    def normalize_reference_lists(
        cls,
        value: list[str],
        info: Any,
    ) -> list[str]:
        return _unique_non_blank(value, info.field_name)

    @field_validator("question", "category", "question_type")
    @classmethod
    def normalize_required_text(cls, value: str, info: Any) -> str:
        return _non_blank(value, info.field_name)

    @model_validator(mode="after")
    def validate_ground_truth_state(self) -> Self:
        article_codes = set(self.expected_article_codes)
        chunk_ids = set(self.evidence_chunk_ids)

        if self.expected_status == AnswerStatus.ANSWERABLE:
            if not article_codes or not chunk_ids or not self.evidence_excerpt.strip():
                raise ValueError(
                    "answerable cases require article codes, chunks and evidence"
                )
        elif article_codes or self.expected_article_ids:
            raise ValueError(
                "non-answerable cases must not declare expected legal citations"
            )
        if self.expected_status == AnswerStatus.OUT_OF_SCOPE:
            if chunk_ids or self.evidence_excerpt.strip():
                raise ValueError(
                    "out-of-scope cases must not declare legal evidence"
                )
        if self.expected_status != AnswerStatus.ANSWERABLE:
            if self.required_claims:
                raise ValueError(
                    "non-answerable cases must not declare required claims"
                )

        claim_ids = [claim.claim_id for claim in self.required_claims]
        claim_ids.extend(claim.claim_id for claim in self.forbidden_claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id values must be unique within a question")

        for claim in self.required_claims:
            if not set(claim.supported_by_article_codes).issubset(article_codes):
                raise ValueError(
                    "required claim cites an article outside expected_article_codes"
                )
            if claim.supported_by_chunk_ids and not set(
                claim.supported_by_chunk_ids
            ).issubset(chunk_ids):
                raise ValueError(
                    "required claim cites a chunk outside evidence_chunk_ids"
                )

        is_adjudicated = self.label_status == LabelStatus.HUMAN_ADJUDICATED
        if is_adjudicated and not self.reviewer.strip():
            raise ValueError("human-adjudicated cases require a reviewer")
        if self.authority_review_status != AuthorityReviewStatus.PENDING:
            if not is_adjudicated or not self.reviewer.strip():
                raise ValueError(
                    "authority review decisions require human adjudication"
                )
        if self.benchmark_enabled:
            if not is_adjudicated:
                raise ValueError(
                    "benchmark cases must be human-adjudicated before use"
                )
            if self.expected_status == AnswerStatus.ANSWERABLE:
                if not self.required_claims:
                    raise ValueError(
                        "answerable benchmark cases require atomic claims"
                    )
        return self


class AnswerQualityDataset(BaseModel):
    """Versioned collection of answer-quality evaluation cases."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["answer-quality-dataset-v1"] = (
        "answer-quality-dataset-v1"
    )
    dataset_status: DatasetStatus = DatasetStatus.DRAFT_CANDIDATE
    locked: bool = False
    authority_review_status: AuthorityReviewStatus = (
        AuthorityReviewStatus.PENDING
    )
    source_corpus_release_id: str = Field(min_length=1)
    source_corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_corpus_path: str = Field(min_length=1)
    source_dataset_path: str = Field(min_length=1)
    source_dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    questions: list[AnswerQualityQuestion] = Field(min_length=1)

    @field_validator(
        "source_corpus_release_id",
        "source_corpus_path",
        "source_dataset_path",
    )
    @classmethod
    def normalize_provenance_text(cls, value: str, info: Any) -> str:
        return _non_blank(value, info.field_name)

    @model_validator(mode="after")
    def validate_dataset_state(self) -> Self:
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question ids must be unique")

        if self.locked != (self.dataset_status == DatasetStatus.LOCKED):
            raise ValueError("locked must match dataset_status=locked")

        if self.dataset_status in {
            DatasetStatus.HUMAN_ADJUDICATED,
            DatasetStatus.LOCKED,
        }:
            if any(
                question.label_status != LabelStatus.HUMAN_ADJUDICATED
                for question in self.questions
            ):
                raise ValueError(
                    "human-adjudicated or locked datasets require adjudicated items"
                )

        if self.authority_review_status != AuthorityReviewStatus.PENDING:
            if any(
                question.authority_review_status
                != self.authority_review_status
                for question in self.questions
            ):
                raise ValueError(
                    "dataset authority status must match every question"
                )
        return self


class DatasetValidationError(ValueError):
    """Raised when an answer-quality dataset cannot be validated."""


def load_answer_quality_dataset(path: str | Path) -> AnswerQualityDataset:
    """Load and strictly validate a UTF-8 JSON evaluation dataset."""

    dataset_path = Path(path)
    try:
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
        return AnswerQualityDataset.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise DatasetValidationError(
            f"invalid answer-quality dataset: {dataset_path}"
        ) from exc
