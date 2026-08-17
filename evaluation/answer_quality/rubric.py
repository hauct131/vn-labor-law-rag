"""Human-review rubric for grounded Vietnamese labour-law answers.

The rubric deliberately separates deterministic checks from legal review. A
high score is not an authority approval; authority approval is recorded on the
evaluation dataset by a qualified reviewer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field


RUBRIC_MAX_SCORE = 10
DEFAULT_PASS_SCORE = 8
MINIMUM_CASE_SCORE = 6


class AnswerRubricScore(BaseModel):
    """Scores assigned by a human reviewer to one generated answer."""

    model_config = ConfigDict(extra="forbid")

    legal_correctness: int = Field(
        ge=0,
        le=4,
        description="Correctness under the supplied legal authority (0-4).",
    )
    groundedness: int = Field(
        ge=0,
        le=2,
        description="Degree to which the answer is grounded in evidence (0-2).",
    )
    citation_accuracy: int = Field(
        ge=0,
        le=2,
        description="Accuracy of cited article/clause/point references (0-2).",
    )
    completeness: int = Field(
        ge=0,
        le=1,
        description="Coverage of material conditions and exceptions (0-1).",
    )
    no_unsupported_information: int = Field(
        ge=0,
        le=1,
        description="No material information beyond the supplied sources (0-1).",
    )
    reviewer_notes: str = Field(default="", max_length=4000)

    @computed_field
    @property
    def total(self) -> int:
        """Return the score on the fixed ten-point scale."""

        return (
            self.legal_correctness
            + self.groundedness
            + self.citation_accuracy
            + self.completeness
            + self.no_unsupported_information
        )

    def passes(self, minimum: int = DEFAULT_PASS_SCORE) -> bool:
        """Return whether this review meets an internal threshold."""

        if not 0 <= minimum <= RUBRIC_MAX_SCORE:
            raise ValueError("minimum rubric score must be between 0 and 10")
        return self.total >= minimum


class RubricSummary(BaseModel):
    """Aggregate result for a complete set of human rubric reviews."""

    model_config = ConfigDict(extra="forbid")

    review_count: int = Field(ge=1)
    average_score: float = Field(ge=0, le=RUBRIC_MAX_SCORE)
    minimum_score: int = Field(ge=0, le=RUBRIC_MAX_SCORE)
    passed: bool


def summarize_rubric_scores(
    scores: list[AnswerRubricScore],
    *,
    minimum_average: float = DEFAULT_PASS_SCORE,
    minimum_case: int = MINIMUM_CASE_SCORE,
) -> RubricSummary:
    """Apply the declared internal thresholds to human review scores."""

    if not scores:
        raise ValueError("at least one rubric score is required")
    if not 0 <= minimum_average <= RUBRIC_MAX_SCORE:
        raise ValueError("minimum average score must be between 0 and 10")
    if not 0 <= minimum_case <= RUBRIC_MAX_SCORE:
        raise ValueError("minimum case score must be between 0 and 10")

    totals = [score.total for score in scores]
    average = sum(totals) / len(totals)
    lowest = min(totals)
    return RubricSummary(
        review_count=len(scores),
        average_score=average,
        minimum_score=lowest,
        passed=average >= minimum_average and lowest >= minimum_case,
    )
