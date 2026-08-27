"""Unit tests for Unified Fail-Closed Runtime Verifier Engine."""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from evaluation.answer_quality.runtime_verification import (
    calculate_case_metrics,
    compute_summary,
    atomic_write_text,
    compute_sha256,
    run_verification,
    VerificationError,
    PreflightValidationError,
)


def test_no_citation_case_metrics():
    """Verify cases without citations return citation_ids_valid=null and NOT_APPLICABLE status."""
    q_item = {
        "id": "case_test_no_cit",
        "question": "Câu hỏi thử nghiệm",
        "expected_status": "answerable",
        "expected_article_codes": ["NĐ128"],
        "evidence_chunk_ids": [],
    }
    data = {
        "answer": "Không đủ căn cứ trong dữ liệu được cung cấp để trả lời chắc chắn.",
        "sources": [],
        "insufficient_evidence": True,
        "fallback_reason": "insufficient_supported_claims",
    }
    canonical_chunks = {"chunk-1", "chunk-2"}

    res = calculate_case_metrics(
        status=200,
        data=data,
        q_item=q_item,
        canonical_chunk_ids=canonical_chunks,
    )

    assert res["citation_present"] is False
    assert res["citation_ids_valid"] is None
    assert res["citation_id_validation_status"] == "NOT_APPLICABLE"


def test_citation_case_metrics_pass():
    """Verify cases with valid canonical citations return citation_ids_valid=True."""
    q_item = {
        "id": "case_test_cit_pass",
        "question": "Câu hỏi thử nghiệm",
        "expected_status": "answerable",
        "expected_article_codes": ["NĐ128"],
        "evidence_chunk_ids": ["chunk-1"],
    }
    data = {
        "answer": "Trả lời căn cứ theo [S1].",
        "sources": [
            {"chunk_id": "chunk-1", "article_code": "NĐ128"}
        ],
    }
    canonical_chunks = {"chunk-1", "chunk-2"}

    res = calculate_case_metrics(
        status=200,
        data=data,
        q_item=q_item,
        canonical_chunk_ids=canonical_chunks,
    )

    assert res["citation_present"] is True
    assert res["citation_ids_valid"] is True
    assert res["citation_id_validation_status"] == "PASS"
    assert res["expected_evidence_hit"] is True
    assert res["grounded_rag_success"] is True


def test_compute_summary_applicable_denominator():
    """Verify compute_summary uses applicable denominator for citation validity."""
    cases = [
        # Case 1: Has valid citation
        {
            "transport_success": True,
            "answer_generated_success": True,
            "citation_present": True,
            "citation_ids_valid": True,
            "expected_evidence_hit": True,
            "grounded_rag_success": True,
            "citation_precision": 1.0,
            "citation_completeness": 1.0,
        },
        # Case 2: No citation (insufficient evidence fallback)
        {
            "transport_success": True,
            "answer_generated_success": False,
            "citation_present": False,
            "citation_ids_valid": None,
            "expected_evidence_hit": False,
            "grounded_rag_success": False,
            "citation_precision": 0.0,
            "citation_completeness": 0.0,
        },
    ]

    summary = compute_summary(cases)
    assert summary["total_cases"] == 2
    assert summary["applicable_citation_case_count"] == 1
    assert summary["not_applicable_citation_case_count"] == 1
    assert summary["citation_id_validity_rate"] == 1.0


def test_atomic_write_text():
    """Verify atomic_write_text creates target file with correct content."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        target = Path(tmp_dir) / "sub" / "test.json"
        content = '{"status": "ok"}'
        atomic_write_text(target, content)

        assert target.exists()
        assert target.read_text(encoding="utf-8") == content


def test_provisional_flag_required():
    """Verify run_verification raises error if provisional_diagnostic_mode is False."""
    with pytest.raises(VerificationError, match="Explicit CLI flag"):
        run_verification(
            dataset_path=Path("dummy"),
            corpus_path=Path("dummy"),
            output_base_dir=Path("dummy"),
            target_url="http://localhost:8000",
            provisional_diagnostic_mode=False,
        )


def test_join_invariance_shuffled_orders():
    """Task 14: Verify metric calculations remain 100% identical regardless of dataset or response list order."""
    import random

    q1 = {
        "id": "c1",
        "question": "Q1",
        "expected_status": "answerable",
        "expected_article_codes": ["A1"],
        "evidence_chunk_ids": ["chk1"],
    }
    q2 = {
        "id": "c2",
        "question": "Q2",
        "expected_status": "answerable",
        "expected_article_codes": ["A2"],
        "evidence_chunk_ids": ["chk2"],
    }

    raw1 = {
        "case_id": "c1",
        "question": "Q1",
        "http_status": 200,
        "answer": "Answer 1",
        "sources": [{"chunk_id": "chk1", "article_code": "A1"}],
    }
    raw2 = {
        "case_id": "c2",
        "question": "Q2",
        "http_status": 200,
        "answer": "Answer 2",
        "sources": [{"chunk_id": "chk2", "article_code": "A2"}],
    }

    canonical = {"chk1", "chk2"}

    # Base order calculation
    res1 = calculate_case_metrics(status=200, data=raw1, q_item=q1, canonical_chunk_ids=canonical)
    res2 = calculate_case_metrics(status=200, data=raw2, q_item=q2, canonical_chunk_ids=canonical)
    base_summary = compute_summary([res1, res2])

    # Shuffled order calculation
    questions_shuffled = [q2, q1]
    responses_shuffled = [raw1, raw2]

    # Join strictly by case_id
    resp_map = {r["case_id"]: r for r in responses_shuffled}
    shuffled_results = [
        calculate_case_metrics(status=200, data=resp_map[q["id"]], q_item=q, canonical_chunk_ids=canonical)
        for q in questions_shuffled
    ]
    shuffled_summary = compute_summary(shuffled_results)

    assert base_summary["grounded_rag_success_rate"] == shuffled_summary["grounded_rag_success_rate"] == 1.0
    assert base_summary["expected_evidence_hit_rate"] == shuffled_summary["expected_evidence_hit_rate"] == 1.0


def test_case_062_wrong_article_produces_evidence_hit_false():
    """Task 15: Verify associating labor_candidate_062 with 20.2.LQ.136 produces expected_evidence_hit=False."""
    q_item = {
        "id": "labor_candidate_062",
        "question": "Đối với công việc trong hầm lò, làm thêm giờ được quy định như thế nào?",
        "expected_status": "answerable",
        "expected_article_codes": ["20.2.TT.3.5"],
        "evidence_chunk_ids": ["c30e738d-5579-5405-9da5-f09be278692c"],
    }

    # Wrong data citing 20.2.LQ.136 instead of 20.2.TT.3.5
    raw_data_wrong = {
        "answer": "Answer citing wrong article 20.2.LQ.136",
        "sources": [
            {"chunk_id": "c579875b-5535-502d-93af-b0a104b09e96", "article_code": "20.2.LQ.136"}
        ],
    }

    canonical = {"c30e738d-5579-5405-9da5-f09be278692c", "c579875b-5535-502d-93af-b0a104b09e96"}

    res = calculate_case_metrics(
        status=200,
        data=raw_data_wrong,
        q_item=q_item,
        canonical_chunk_ids=canonical,
    )

    assert res["article_hit"] is False
    assert res["chunk_hit"] is False
    assert res["expected_evidence_hit"] is False
    assert res["grounded_rag_success"] is False
