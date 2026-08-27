"""Strict Pydantic schema for human adjudication artifacts and reviewer decisions.

A human adjudication artifact captures explicit decisions (accept/edit/reject)
by a named human reviewer. It enforces fail-closed validation: no AI/model names
are allowed as reviewers, and authority review status remains pending.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from evaluation.answer_quality.schema import (
    AuthorityReviewStatus,
    ForbiddenClaim,
    RequiredClaim,
    _non_blank,
)


FORBIDDEN_REVIEWER_PATTERNS = [
    r"\bai\b",
    r"grok",
    r"claude",
    r"chatgpt",
    r"gpt",
    r"antigravity",
    r"openai",
    r"gemini",
    r"llama",
    r"deepseek",
    r"copilot",
    r"mistral",
    r"nemotron",
    r"\bllm\b",
    r"\bmodel\b",
    r"\bbot\b",
    r"\bauto\b",
]


def validate_reviewer_name(reviewer: str) -> str:
    """Ensure reviewer name is non-blank and not a fake/AI model identifier."""
    normalized = _non_blank(reviewer, "reviewer")
    lowered = normalized.lower()
    for pattern in FORBIDDEN_REVIEWER_PATTERNS:
        if re.search(pattern, lowered):
            raise ValueError(
                f"reviewer name '{reviewer}' is invalid (AI/model names are forbidden)"
            )
    return normalized


class HumanAdjudicationCase(BaseModel):
    """Human review decision and edited claims for a single question case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    decision: Literal["accept", "edit", "reject"]
    reviewer_notes: str = Field(default="", max_length=4000)
    edited_required_claims: list[RequiredClaim] = Field(default_factory=list)
    edited_forbidden_claims: list[ForbiddenClaim] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_case_decision(self) -> Self:
        if self.decision == "accept":
            if self.edited_required_claims or self.edited_forbidden_claims:
                raise ValueError(
                    f"case {self.case_id}: accept decision must not supply edited claims"
                )
        return self


class HumanAdjudicationArtifact(BaseModel):
    """Container for human review decisions covering evaluation dataset cases."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["answer-human-adjudication-v1"] = (
        "answer-human-adjudication-v1"
    )
    adjudication_status: Literal["human_adjudicated"] = "human_adjudicated"
    authority_review_status: Literal[AuthorityReviewStatus.PENDING] = (
        AuthorityReviewStatus.PENDING
    )
    golden_locked: Literal[False] = False
    reviewer: str = Field(min_length=1, max_length=200)
    reviewed_at: str = Field(min_length=1, max_length=100)
    cases: list[HumanAdjudicationCase] = Field(min_length=1)

    @field_validator("reviewer")
    @classmethod
    def check_reviewer(cls, value: str) -> str:
        return validate_reviewer_name(value)

    @field_validator("reviewed_at")
    @classmethod
    def check_reviewed_at(cls, value: str) -> str:
        return _non_blank(value, "reviewed_at")

    @model_validator(mode="after")
    def validate_cases_list(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("human adjudication case IDs must be unique")
        return self
