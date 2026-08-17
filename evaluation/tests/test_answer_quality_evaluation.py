"""Tests for answer/citation dataset safety, rubric and metrics."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from evaluation.answer_quality.metrics import (
    AnswerEvaluationObservation,
    AnswerQualityThresholds,
    aggregate_case_metrics,
    evaluate_observation,
    evaluate_technical_gate,
)
from evaluation.answer_quality.schema import (
    AnswerQualityDataset,
    AnswerQualityQuestion,
    DatasetValidationError,
    load_answer_quality_dataset,
)
from evaluation.answer_quality.rubric import (
    AnswerRubricScore,
    summarize_rubric_scores,
)
from evaluation.answer_quality.schema import AnswerStatus
from evaluation.answer_quality.validate_dataset import run as validate_dataset


CORPUS_SHA256 = "a" * 64


def answerable_question(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "aq-001",
        "question": "Người lao động có quyền nghỉ theo quy định nào?",
        "category": "working_time_rest",
        "question_type": "direct_rule",
        "difficulty": "easy",
        "expected_status": "answerable",
        "expected_article_codes": ["20.2.LQ.35"],
        "expected_article_ids": ["vn:law:article:35"],
        "evidence_chunk_ids": ["chunk-35"],
        "evidence_excerpt": "Điều 35 quy định quyền của người lao động.",
        "required_claims": [],
        "forbidden_claims": [],
        "label_status": "pending_human_extraction",
        "authority_review_status": "pending",
        "reviewer": "",
        "review_notes": "",
        "benchmark_enabled": False,
    }
    payload.update(overrides)
    return payload


def test_rubric_uses_fixed_ten_point_scale() -> None:
    score = AnswerRubricScore(
        legal_correctness=4,
        groundedness=2,
        citation_accuracy=2,
        completeness=1,
        no_unsupported_information=1,
    )

    assert score.total == 10
    assert score.passes()


def test_rubric_rejects_out_of_range_scores() -> None:
    with pytest.raises(ValidationError):
        AnswerRubricScore(
            legal_correctness=5,
            groundedness=2,
            citation_accuracy=2,
            completeness=1,
            no_unsupported_information=1,
        )


def test_rubric_summary_requires_average_and_case_floor() -> None:
    strong = AnswerRubricScore(
        legal_correctness=4,
        groundedness=2,
        citation_accuracy=2,
        completeness=1,
        no_unsupported_information=1,
    )
    weak = AnswerRubricScore(
        legal_correctness=2,
        groundedness=1,
        citation_accuracy=1,
        completeness=0,
        no_unsupported_information=1,
    )

    summary = summarize_rubric_scores([strong, weak])

    assert summary.average_score == 7.5
    assert summary.minimum_score == 5
    assert summary.passed is False


def test_candidate_can_remain_pending_without_invented_claims() -> None:
    question = AnswerQualityQuestion.model_validate(answerable_question())

    assert question.expected_status == AnswerStatus.ANSWERABLE
    assert question.required_claims == []
    assert question.benchmark_enabled is False


def test_answerable_benchmark_requires_human_claims_and_reviewer() -> None:
    with pytest.raises(ValidationError, match="human-adjudicated"):
        AnswerQualityQuestion.model_validate(
            answerable_question(benchmark_enabled=True)
        )

    with pytest.raises(ValidationError, match="atomic claims"):
        AnswerQualityQuestion.model_validate(
            answerable_question(
                benchmark_enabled=True,
                label_status="human_adjudicated",
                reviewer="Nguyen A",
            )
        )


def test_non_answerable_case_cannot_claim_expected_legal_sources() -> None:
    with pytest.raises(ValidationError, match="must not declare"):
        AnswerQualityQuestion.model_validate(
            answerable_question(expected_status="out_of_scope")
        )


def test_insufficient_evidence_can_record_retrieved_but_inadequate_context() -> None:
    question = AnswerQualityQuestion.model_validate(
        answerable_question(
            expected_status="insufficient_evidence",
            expected_article_codes=[],
            expected_article_ids=[],
            evidence_chunk_ids=["retrieved-but-inadequate"],
            evidence_excerpt="Đoạn truy hồi không đủ căn cứ để kết luận.",
        )
    )

    assert question.expected_status == AnswerStatus.INSUFFICIENT_EVIDENCE
    assert question.expected_article_codes == []


def test_out_of_scope_case_rejects_legal_evidence() -> None:
    with pytest.raises(ValidationError, match="must not declare legal evidence"):
        AnswerQualityQuestion.model_validate(
            answerable_question(
                expected_status="out_of_scope",
                expected_article_codes=[],
                expected_article_ids=[],
            )
        )


def test_claim_must_reference_only_case_evidence() -> None:
    with pytest.raises(ValidationError, match="outside expected_article_codes"):
        AnswerQualityQuestion.model_validate(
            answerable_question(
                required_claims=[
                    {
                        "claim_id": "c1",
                        "text": "Một nhận định.",
                        "supported_by_article_codes": ["UNKNOWN"],
                        "supported_by_chunk_ids": ["chunk-35"],
                    }
                ]
            )
        )


def test_locked_dataset_rejects_multi_llm_only_labels() -> None:
    with pytest.raises(ValidationError, match="adjudicated items"):
        AnswerQualityDataset.model_validate(
            {
                "dataset_status": "locked",
                "locked": True,
                "authority_review_status": "pending",
                "source_corpus_release_id": "release-1",
                "source_corpus_sha256": CORPUS_SHA256,
                "questions": [
                    answerable_question(label_status="multi_llm_candidate")
                ],
            }
        )


def test_dataset_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError):
        AnswerQualityDataset.model_validate(
            {
                "schema_version": "answer-quality-dataset-v999",
                "dataset_status": "draft_candidate",
                "locked": False,
                "authority_review_status": "pending",
                "source_corpus_release_id": "release-1",
                "source_corpus_sha256": CORPUS_SHA256,
                "questions": [answerable_question()],
            }
        )


def test_loader_wraps_invalid_json(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(DatasetValidationError):
        load_answer_quality_dataset(path)


def test_loader_accepts_valid_pending_candidate(tmp_path) -> None:
    path = tmp_path / "candidate.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "answer-quality-dataset-v1",
                "dataset_status": "draft_candidate",
                "locked": False,
                "authority_review_status": "pending",
                "source_corpus_release_id": "release-1",
                "source_corpus_sha256": CORPUS_SHA256,
                "questions": [answerable_question()],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    dataset = load_answer_quality_dataset(path)

    assert dataset.questions[0].id == "aq-001"
    assert dataset.locked is False

    report = validate_dataset(path)
    assert report["status"] == "PASS"
    assert report["question_count"] == 1
    assert report["expected_status_counts"] == {"answerable": 1}


def test_deterministic_metrics_expose_citation_and_claim_failures() -> None:
    metrics = evaluate_observation(
        AnswerEvaluationObservation(
            case_id="aq-001",
            expected_status="answerable",
            predicted_status="answerable",
            available_source_ids={"S1", "S2"},
            declared_source_ids={"S1", "S9"},
            inline_source_ids={"S1", "S9"},
            expected_article_codes={"A", "B"},
            cited_article_codes={"A", "C"},
            required_claim_ids={"c1", "c2"},
            satisfied_claim_ids={"c1"},
            unsupported_material_claim_count=1,
        )
    )

    assert metrics.status_accuracy == 1.0
    assert metrics.citation_id_validity == 0.5
    assert metrics.inline_declared_match == 1.0
    assert metrics.citation_precision == 0.5
    assert metrics.citation_completeness == 0.5
    assert metrics.required_claim_recall == 0.5
    assert metrics.unsupported_material_claim_count == 1


def test_non_answerable_case_leaves_article_metrics_unscored() -> None:
    metrics = evaluate_observation(
        AnswerEvaluationObservation(
            case_id="aq-oos-001",
            expected_status="out_of_scope",
            predicted_status="out_of_scope",
        )
    )

    assert metrics.status_accuracy == 1.0
    assert metrics.citation_id_validity == 1.0
    assert metrics.citation_precision is None
    assert metrics.citation_completeness is None
    assert metrics.required_claim_recall is None


def test_aggregate_ignores_dimensions_that_are_not_scoreable() -> None:
    answerable = evaluate_observation(
        AnswerEvaluationObservation(
            case_id="aq-001",
            expected_status="answerable",
            predicted_status="answerable",
            available_source_ids={"S1"},
            declared_source_ids={"S1"},
            inline_source_ids={"S1"},
            expected_article_codes={"A"},
            cited_article_codes={"A"},
            required_claim_ids={"c1"},
            satisfied_claim_ids={"c1"},
        )
    )
    out_of_scope = evaluate_observation(
        AnswerEvaluationObservation(
            case_id="aq-oos-001",
            expected_status="out_of_scope",
            predicted_status="insufficient_evidence",
        )
    )

    aggregate = aggregate_case_metrics([answerable, out_of_scope])

    assert aggregate.case_count == 2
    assert aggregate.status_accuracy == 0.5
    assert aggregate.citation_precision == 1.0
    assert aggregate.citation_completeness == 1.0
    assert aggregate.required_claim_recall == 1.0


def test_satisfied_claims_must_be_declared_in_ground_truth() -> None:
    with pytest.raises(ValidationError, match="subset"):
        AnswerEvaluationObservation(
            case_id="aq-001",
            expected_status="answerable",
            predicted_status="answerable",
            required_claim_ids={"c1"},
            satisfied_claim_ids={"c2"},
        )


def test_technical_gate_passes_complete_clean_metrics() -> None:
    case = evaluate_observation(
        AnswerEvaluationObservation(
            case_id="aq-001",
            expected_status="answerable",
            predicted_status="answerable",
            available_source_ids={"S1"},
            declared_source_ids={"S1"},
            inline_source_ids={"S1"},
            expected_article_codes={"A"},
            cited_article_codes={"A"},
            required_claim_ids={"c1"},
            satisfied_claim_ids={"c1"},
        )
    )

    result = evaluate_technical_gate(aggregate_case_metrics([case]))

    assert result.status == "PASS"
    assert result.failed_checks == []


def test_technical_gate_fails_closed_when_claims_are_unscored() -> None:
    case = evaluate_observation(
        AnswerEvaluationObservation(
            case_id="aq-oos-001",
            expected_status="out_of_scope",
            predicted_status="out_of_scope",
        )
    )

    result = evaluate_technical_gate(
        aggregate_case_metrics([case]),
        AnswerQualityThresholds(),
    )

    assert result.status == "FAIL"
    assert "required_claim_recall:not_scoreable" in result.failed_checks
