from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.answer_quality.apply_human_review import (
    DEFAULT_DATASET,
    HumanReviewApplicationError,
    build_human_adjudicated_dataset,
)
from evaluation.answer_quality.create_human_review_packet import (
    PacketCreationError,
    build_human_review_packet,
)
from evaluation.answer_quality.human_schema import (
    HumanAdjudicationArtifact,
    HumanAdjudicationCase,
    validate_reviewer_name,
)
from evaluation.answer_quality.lock_human_adjudicated_dataset import (
    DatasetLockError,
    lock_dataset,
)
from evaluation.answer_quality.schema import (
    AnswerQualityDataset,
    AuthorityReviewStatus,
    DatasetStatus,
    LabelStatus,
    load_answer_quality_dataset,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _valid_review_dict(dataset: AnswerQualityDataset, reviewer: str = "Trịnh Văn Thanh") -> dict:
    return {
        "schema_version": "answer-human-adjudication-v1",
        "adjudication_status": "human_adjudicated",
        "authority_review_status": "pending",
        "golden_locked": False,
        "reviewer": reviewer,
        "reviewed_at": "2026-08-18T00:00:00Z",
        "cases": [
            {
                "case_id": q.id,
                "decision": "accept",
                "reviewer_notes": "Accepted by human reviewer",
                "edited_required_claims": [],
                "edited_forbidden_claims": [],
            }
            for q in dataset.questions
        ],
    }


def test_packet_creation_contains_20_cases_and_full_canonical_chunks() -> None:
    dataset_path = REPO_ROOT / DEFAULT_DATASET
    packet_json, markdown_str, manifest = build_human_review_packet(
        dataset_path,
        repo_root=REPO_ROOT,
    )

    assert len(packet_json["cases"]) == 20
    assert len(packet_json["cases_detail"]) == 20
    assert manifest["case_count"] == 20
    assert manifest["authority_review_status"] == "pending"
    assert manifest["golden_locked"] is False

    # Check evidence chunk content in detail
    first_case = packet_json["cases_detail"][0]
    assert len(first_case["evidence_chunks"]) > 0
    first_chunk = first_case["evidence_chunks"][0]
    assert "content" in first_chunk
    assert len(first_chunk["content"].strip()) > 10
    assert "Điều" in first_chunk["content"] or "người lao động" in first_chunk["content"].lower()

    # Markdown contains text & full canonical content
    assert "### Case: labor_candidate_046" in markdown_str
    assert "Evidence Chunks (Full Canonical Content)" in markdown_str


def test_reviewer_name_validation_rejects_ai_names() -> None:
    for ai_name in ["AI", "Grok-3", "Claude 3.5 Sonnet", "ChatGPT", "Antigravity", "OpenAI", "Gemini Pro", "Llama3"]:
        with pytest.raises(ValueError, match="forbidden"):
            validate_reviewer_name(ai_name)

    assert validate_reviewer_name("Trần Thị Mai") == "Trần Thị Mai"
    assert validate_reviewer_name("Chuyên viên Nguyễn Văn A") == "Chuyên viên Nguyễn Văn A"


def test_empty_reviewer_fails_validation() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        validate_reviewer_name("")


def test_accept_decision_with_edited_claims_fails_validation() -> None:
    with pytest.raises(ValueError, match="accept decision must not supply edited claims"):
        HumanAdjudicationCase.model_validate({
            "case_id": "labor_candidate_046",
            "decision": "accept",
            "reviewer_notes": "notes",
            "edited_required_claims": [
                {
                    "claim_id": "C1",
                    "text": "some claim",
                    "supported_by_article_codes": ["20.2.LQ.35"],
                    "supported_by_chunk_ids": ["c1"],
                }
            ],
        })


def test_apply_human_review_rejects_missing_case(tmp_path: Path) -> None:
    dataset = load_answer_quality_dataset(REPO_ROOT / DEFAULT_DATASET)
    review = _valid_review_dict(dataset)
    review["cases"].pop()  # Missing 1 case
    
    review_file = tmp_path / "review.json"
    review_file.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(HumanReviewApplicationError, match="coverage mismatch"):
        build_human_adjudicated_dataset(
            REPO_ROOT / DEFAULT_DATASET,
            review_file,
            repo_root=REPO_ROOT,
        )


def test_apply_human_review_rejects_duplicate_case(tmp_path: Path) -> None:
    dataset = load_answer_quality_dataset(REPO_ROOT / DEFAULT_DATASET)
    review = _valid_review_dict(dataset)
    review["cases"].append(review["cases"][0].copy())  # Duplicate case
    
    review_file = tmp_path / "review.json"
    review_file.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(HumanReviewApplicationError, match="invalid human adjudication artifact"):
        build_human_adjudicated_dataset(
            REPO_ROOT / DEFAULT_DATASET,
            review_file,
            repo_root=REPO_ROOT,
        )


def test_apply_human_review_fails_closed_on_reject_decision(tmp_path: Path) -> None:
    dataset = load_answer_quality_dataset(REPO_ROOT / DEFAULT_DATASET)
    review = _valid_review_dict(dataset)
    review["cases"][0]["decision"] = "reject"  # Fail closed case
    
    review_file = tmp_path / "review.json"
    review_file.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(HumanReviewApplicationError, match="failed closed"):
        build_human_adjudicated_dataset(
            REPO_ROOT / DEFAULT_DATASET,
            review_file,
            repo_root=REPO_ROOT,
        )


def test_apply_human_review_rejects_edit_outside_citation_allowlist(tmp_path: Path) -> None:
    dataset = load_answer_quality_dataset(REPO_ROOT / DEFAULT_DATASET)
    review = _valid_review_dict(dataset)
    review["cases"][0]["decision"] = "edit"
    review["cases"][0]["edited_required_claims"] = [
        {
            "claim_id": "C1_EDIT",
            "text": "Edited required claim",
            "supported_by_article_codes": ["INVALID_ARTICLE"],
            "supported_by_chunk_ids": [dataset.questions[0].evidence_chunk_ids[0]],
        }
    ]
    
    review_file = tmp_path / "review.json"
    review_file.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(HumanReviewApplicationError, match="outside allowlist"):
        build_human_adjudicated_dataset(
            REPO_ROOT / DEFAULT_DATASET,
            review_file,
            repo_root=REPO_ROOT,
        )


def test_apply_human_review_rejects_edit_non_existent_chunk(tmp_path: Path) -> None:
    dataset = load_answer_quality_dataset(REPO_ROOT / DEFAULT_DATASET)
    review = _valid_review_dict(dataset)
    review["cases"][0]["decision"] = "edit"
    review["cases"][0]["edited_required_claims"] = [
        {
            "claim_id": "C1_EDIT",
            "text": "Edited claim text",
            "supported_by_article_codes": [dataset.questions[0].expected_article_codes[0]],
            "supported_by_chunk_ids": ["non-existent-chunk-id-123"],
        }
    ]
    
    review_file = tmp_path / "review.json"
    review_file.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(HumanReviewApplicationError, match="outside allowlist"):
        build_human_adjudicated_dataset(
            REPO_ROOT / DEFAULT_DATASET,
            review_file,
            repo_root=REPO_ROOT,
        )


def test_apply_human_review_rejects_wrong_corpus_hash(tmp_path: Path) -> None:
    dataset_dict = json.loads((REPO_ROOT / DEFAULT_DATASET).read_text(encoding="utf-8"))
    dataset_dict["source_corpus_sha256"] = "a" * 64
    invalid_dataset_file = tmp_path / "invalid_dataset.json"
    invalid_dataset_file.write_text(json.dumps(dataset_dict, ensure_ascii=False), encoding="utf-8")

    dataset = load_answer_quality_dataset(REPO_ROOT / DEFAULT_DATASET)
    review_file = tmp_path / "review.json"
    review_file.write_text(json.dumps(_valid_review_dict(dataset), ensure_ascii=False), encoding="utf-8")

    with pytest.raises(HumanReviewApplicationError, match="corpus hash mismatch"):
        build_human_adjudicated_dataset(
            invalid_dataset_file,
            review_file,
            repo_root=REPO_ROOT,
        )


def test_multi_llm_dataset_cannot_enable_benchmark() -> None:
    dataset = load_answer_quality_dataset(REPO_ROOT / DEFAULT_DATASET)
    assert dataset.dataset_status == DatasetStatus.MULTI_LLM_REVIEWED
    assert dataset.locked is False
    assert all(q.benchmark_enabled is False for q in dataset.questions)


def test_cannot_lock_unadjudicated_dataset() -> None:
    with pytest.raises(DatasetLockError, match="must be 'human_adjudicated'"):
        lock_dataset(
            REPO_ROOT / DEFAULT_DATASET,
            repo_root=REPO_ROOT,
            confirm_lock=True,
        )


def test_locking_requires_confirm_lock_flag(tmp_path: Path) -> None:
    dataset = load_answer_quality_dataset(REPO_ROOT / DEFAULT_DATASET)
    review_file = tmp_path / "review.json"
    review_file.write_text(json.dumps(_valid_review_dict(dataset), ensure_ascii=False), encoding="utf-8")
    
    adjudicated = build_human_adjudicated_dataset(
        REPO_ROOT / DEFAULT_DATASET,
        review_file,
        repo_root=REPO_ROOT,
    )
    adj_file = tmp_path / "adjudicated.json"
    adj_file.write_text(json.dumps(adjudicated.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DatasetLockError, match="locking requires explicit confirmation"):
        lock_dataset(
            adj_file,
            repo_root=REPO_ROOT,
            confirm_lock=False,
        )


def test_successful_human_adjudication_and_locking_workflow(tmp_path: Path) -> None:
    dataset = load_answer_quality_dataset(REPO_ROOT / DEFAULT_DATASET)
    review_file = tmp_path / "review.json"
    review_file.write_text(json.dumps(_valid_review_dict(dataset, reviewer="Luật sư Lê Hoàng"), ensure_ascii=False), encoding="utf-8")

    # Apply human review
    adjudicated = build_human_adjudicated_dataset(
        REPO_ROOT / DEFAULT_DATASET,
        review_file,
        repo_root=REPO_ROOT,
    )

    assert adjudicated.dataset_status == DatasetStatus.HUMAN_ADJUDICATED
    assert adjudicated.locked is False
    assert adjudicated.authority_review_status == AuthorityReviewStatus.PENDING
    assert all(q.label_status == LabelStatus.HUMAN_ADJUDICATED for q in adjudicated.questions)
    assert all(q.reviewer == "Luật sư Lê Hoàng" for q in adjudicated.questions)
    assert all(q.benchmark_enabled is False for q in adjudicated.questions)
    assert all(q.authority_review_status == AuthorityReviewStatus.PENDING for q in adjudicated.questions)

    # Save human adjudicated dataset
    adj_file = tmp_path / "human_adjudicated_questions.json"
    adj_file.write_text(json.dumps(adjudicated.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")

    # Lock dataset with confirm-lock flag
    locked = lock_dataset(
        adj_file,
        repo_root=REPO_ROOT,
        confirm_lock=True,
    )

    assert locked.dataset_status == DatasetStatus.LOCKED
    assert locked.locked is True
    assert locked.authority_review_status == AuthorityReviewStatus.PENDING
    assert all(q.benchmark_enabled is True for q in locked.questions)
    assert all(q.authority_review_status == AuthorityReviewStatus.PENDING for q in locked.questions)
