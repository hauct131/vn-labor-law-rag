"""Strict schema for externally adjudicated answer-label candidates."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evaluation.answer_quality.schema import (
    AnswerStatus,
    AuthorityReviewStatus,
    ForbiddenClaim,
    RequiredClaim,
)


class DisputedPoint(BaseModel):
    """One auditable disagreement and its evidence-based resolution."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=2000)
    resolution: str = Field(min_length=1, max_length=4000)


class ManualAdjudicationCase(BaseModel):
    """One multi-model candidate that still requires human review."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    decision: Literal["select_a", "select_b", "merge", "reject_both"]
    expected_status: AnswerStatus
    required_claims: list[RequiredClaim] = Field(default_factory=list)
    forbidden_claims: list[ForbiddenClaim] = Field(default_factory=list)
    disputed_points: list[DisputedPoint] = Field(default_factory=list)
    adjudication_notes: str = Field(min_length=1, max_length=4000)
    label_status: Literal["multi_llm_candidate"]
    needs_human_review: Literal[True]
    authority_review_status: Literal[AuthorityReviewStatus.PENDING]

    @model_validator(mode="after")
    def validate_claims(self) -> Self:
        claim_ids = [claim.claim_id for claim in self.required_claims]
        claim_ids.extend(claim.claim_id for claim in self.forbidden_claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim IDs must be unique within an adjudicated case")
        if self.expected_status == AnswerStatus.ANSWERABLE:
            if not self.required_claims:
                raise ValueError("answerable adjudication requires required claims")
        elif self.required_claims:
            raise ValueError("non-answerable adjudication cannot require claims")
        return self


class ManualAdjudicationArtifact(BaseModel):
    """A multi-model artifact that is explicitly not a locked golden set."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["answer-manual-adjudication-v1"]
    adjudication_status: Literal["multi_llm_adjudicated_candidate"]
    authority_review_status: Literal[AuthorityReviewStatus.PENDING]
    golden_locked: Literal[False]
    cases: list[ManualAdjudicationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("adjudication case IDs must be unique")
        return self
