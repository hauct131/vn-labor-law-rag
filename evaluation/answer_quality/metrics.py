"""Deterministic per-case and aggregate answer/citation metrics."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evaluation.answer_quality.schema import AnswerStatus


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


class AnswerEvaluationObservation(BaseModel):
    """Normalized facts collected from one real RAG response."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    expected_status: AnswerStatus
    predicted_status: AnswerStatus
    available_source_ids: set[str] = Field(default_factory=set)
    declared_source_ids: set[str] = Field(default_factory=set)
    inline_source_ids: set[str] = Field(default_factory=set)
    expected_article_codes: set[str] = Field(default_factory=set)
    cited_article_codes: set[str] = Field(default_factory=set)
    required_claim_ids: set[str] = Field(default_factory=set)
    satisfied_claim_ids: set[str] = Field(default_factory=set)
    unsupported_material_claim_count: int = Field(default=0, ge=0)
    generation_failed: bool = False

    @model_validator(mode="after")
    def validate_claim_ids(self) -> Self:
        if not self.satisfied_claim_ids.issubset(self.required_claim_ids):
            raise ValueError(
                "satisfied_claim_ids must be a subset of required_claim_ids"
            )
        return self


class AnswerCaseMetrics(BaseModel):
    """Deterministic scores for one observation."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    status_accuracy: float = Field(ge=0, le=1)
    citation_id_validity: float = Field(ge=0, le=1)
    inline_declared_match: float = Field(ge=0, le=1)
    citation_precision: float | None = Field(default=None, ge=0, le=1)
    citation_completeness: float | None = Field(default=None, ge=0, le=1)
    required_claim_recall: float | None = Field(default=None, ge=0, le=1)
    unsupported_material_claim_count: int = Field(ge=0)
    runtime_success: float = Field(ge=0, le=1)


def evaluate_observation(
    observation: AnswerEvaluationObservation,
) -> AnswerCaseMetrics:
    """Compute metrics without making a semantic judgment through an LLM."""

    declared = observation.declared_source_ids
    expected_articles = observation.expected_article_codes
    cited_articles = observation.cited_article_codes

    if declared:
        valid_count = len(declared & observation.available_source_ids)
        citation_id_validity = valid_count / len(declared)
    else:
        citation_id_validity = 1.0

    if cited_articles:
        citation_precision = len(
            cited_articles & expected_articles
        ) / len(cited_articles)
    elif expected_articles:
        citation_precision = 0.0
    else:
        citation_precision = None

    if expected_articles:
        citation_completeness = len(
            cited_articles & expected_articles
        ) / len(expected_articles)
    elif cited_articles:
        citation_completeness = 0.0
    else:
        citation_completeness = None

    return AnswerCaseMetrics(
        case_id=observation.case_id,
        status_accuracy=float(
            observation.expected_status == observation.predicted_status
        ),
        citation_id_validity=citation_id_validity,
        inline_declared_match=float(
            observation.inline_source_ids == declared
        ),
        citation_precision=citation_precision,
        citation_completeness=citation_completeness,
        required_claim_recall=_ratio(
            len(observation.satisfied_claim_ids),
            len(observation.required_claim_ids),
        ),
        unsupported_material_claim_count=(
            observation.unsupported_material_claim_count
        ),
        runtime_success=float(not observation.generation_failed),
    )


class AggregateAnswerMetrics(BaseModel):
    """Macro averages across a complete evaluation run."""

    model_config = ConfigDict(extra="forbid")

    case_count: int = Field(ge=1)
    status_accuracy: float = Field(ge=0, le=1)
    citation_id_validity: float = Field(ge=0, le=1)
    inline_declared_match: float = Field(ge=0, le=1)
    citation_precision: float | None = Field(default=None, ge=0, le=1)
    citation_completeness: float | None = Field(default=None, ge=0, le=1)
    required_claim_recall: float | None = Field(default=None, ge=0, le=1)
    unsupported_material_claim_count: int = Field(ge=0)
    runtime_success_rate: float = Field(ge=0, le=1)


class AnswerQualityThresholds(BaseModel):
    """Explicit internal technical gate; these are not legal standards."""

    model_config = ConfigDict(extra="forbid")

    status_accuracy: float = Field(default=0.90, ge=0, le=1)
    citation_id_validity: float = Field(default=1.0, ge=0, le=1)
    inline_declared_match: float = Field(default=1.0, ge=0, le=1)
    citation_precision: float = Field(default=0.90, ge=0, le=1)
    citation_completeness: float = Field(default=0.90, ge=0, le=1)
    required_claim_recall: float = Field(default=0.90, ge=0, le=1)
    maximum_unsupported_material_claims: int = Field(default=0, ge=0)
    runtime_success_rate: float = Field(default=0.95, ge=0, le=1)


class TechnicalGateResult(BaseModel):
    """Machine-checkable result that remains separate from authority review."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(pattern=r"^(PASS|FAIL)$")
    failed_checks: list[str]


def _mean_available(values: list[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    if not available:
        return None
    return sum(available) / len(available)


def aggregate_case_metrics(
    cases: list[AnswerCaseMetrics],
) -> AggregateAnswerMetrics:
    """Macro-average cases while preserving unscored metric dimensions."""

    if not cases:
        raise ValueError("at least one case metric is required")
    count = len(cases)
    return AggregateAnswerMetrics(
        case_count=count,
        status_accuracy=sum(case.status_accuracy for case in cases) / count,
        citation_id_validity=sum(
            case.citation_id_validity for case in cases
        ) / count,
        inline_declared_match=sum(
            case.inline_declared_match for case in cases
        ) / count,
        citation_precision=_mean_available(
            [case.citation_precision for case in cases]
        ),
        citation_completeness=_mean_available(
            [case.citation_completeness for case in cases]
        ),
        required_claim_recall=_mean_available(
            [case.required_claim_recall for case in cases]
        ),
        unsupported_material_claim_count=sum(
            case.unsupported_material_claim_count for case in cases
        ),
        runtime_success_rate=sum(case.runtime_success for case in cases) / count,
    )


def evaluate_technical_gate(
    metrics: AggregateAnswerMetrics,
    thresholds: AnswerQualityThresholds | None = None,
) -> TechnicalGateResult:
    """Apply explicit thresholds and fail closed on an unscored dimension."""

    required = thresholds or AnswerQualityThresholds()
    failed: list[str] = []

    numeric_checks = {
        "status_accuracy": (metrics.status_accuracy, required.status_accuracy),
        "citation_id_validity": (
            metrics.citation_id_validity,
            required.citation_id_validity,
        ),
        "inline_declared_match": (
            metrics.inline_declared_match,
            required.inline_declared_match,
        ),
        "citation_precision": (
            metrics.citation_precision,
            required.citation_precision,
        ),
        "citation_completeness": (
            metrics.citation_completeness,
            required.citation_completeness,
        ),
        "required_claim_recall": (
            metrics.required_claim_recall,
            required.required_claim_recall,
        ),
        "runtime_success_rate": (
            metrics.runtime_success_rate,
            required.runtime_success_rate,
        ),
    }
    for name, (actual, minimum) in numeric_checks.items():
        if actual is None:
            failed.append(f"{name}:not_scoreable")
        elif actual < minimum:
            failed.append(f"{name}:{actual:.6f}<{minimum:.6f}")

    maximum = required.maximum_unsupported_material_claims
    if metrics.unsupported_material_claim_count > maximum:
        failed.append(
            "unsupported_material_claim_count:"
            f"{metrics.unsupported_material_claim_count}>{maximum}"
        )

    return TechnicalGateResult(
        status="FAIL" if failed else "PASS",
        failed_checks=failed,
    )
