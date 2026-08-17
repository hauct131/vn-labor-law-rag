from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evaluation.answer_quality.apply_human_review import DEFAULT_DATASET
from evaluation.answer_quality.provisional_diagnostic import (
    ProvisionalDiagnosticError,
    _check_forbidden_phrases,
    run_provisional_diagnostic,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_provisional_diagnostic_fails_without_provisional_flag(tmp_path: Path) -> None:
    with pytest.raises(ProvisionalDiagnosticError, match="CLI flag --provisional-multi-llm-diagnostic is required"):
        run_provisional_diagnostic(
            REPO_ROOT / DEFAULT_DATASET,
            "http://localhost:8000/api",
            tmp_path,
            repo_root=REPO_ROOT,
            provisional_flag=False,
        )


def test_forbidden_phrases_check_raises_error() -> None:
    for phrase in ["GOLDEN PASS", "HUMAN APPROVED", "AUTHORITY APPROVED", "LEGALLY VALIDATED"]:
        with pytest.raises(ProvisionalDiagnosticError, match="forbidden conclusion phrase"):
            _check_forbidden_phrases(f"System status: {phrase}")


def test_provisional_diagnostic_execution_and_reports(tmp_path: Path) -> None:
    # Mock RAG HTTP API response
    mock_response_body = json.dumps({
        "answer": "Căn cứ Điều 35 Bộ luật Lao động 2019, người lao động có quyền đơn phương chấm dứt hợp đồng...",
        "sources": [
            {
                "chunk_id": "c1",
                "article_code": "20.2.LQ.35",
                "article_number": "Điều 35",
                "article_title": "Quyền đơn phương chấm dứt hợp đồng lao động của người lao động",
            }
        ],
        "retrieval_ms": 15.0,
        "generation_ms": 1200.0,
        "total_ms": 1215.0,
    }).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = mock_response_body
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        report = run_provisional_diagnostic(
            REPO_ROOT / DEFAULT_DATASET,
            "http://localhost:8000/api",
            tmp_path,
            repo_root=REPO_ROOT,
            provisional_flag=True,
        )

    # Check manifest metadata
    manifest = report["manifest"]
    assert manifest["evaluation_mode"] == "PROVISIONAL_MULTI_LLM_DIAGNOSTIC"
    assert manifest["benchmark_status"] == "NOT_OFFICIAL"
    assert manifest["golden_locked"] is False
    assert manifest["human_adjudication_status"] == "pending"
    assert manifest["authority_review_status"] == "pending"
    assert manifest["official_technical_gate_status"] == "FAIL_CLOSED_OR_NOT_APPLICABLE"

    # Check summary metrics
    summary = report["summary_metrics"]
    assert summary["total_cases"] == 20
    assert summary["runtime_success_rate"] == 1.0
    assert summary["http_runtime_errors"] == 0
    assert summary["required_claim_recall"] == "not_scoreable"
    assert summary["forbidden_claim_violations"] == "not_scoreable"
    assert summary["unsupported_material_claims"] == "not_scoreable"

    # Check generated files
    json_report_file = tmp_path / "provisional-runtime-report.json"
    md_report_file = tmp_path / "provisional-runtime-report.md"
    struct_metrics_file = tmp_path / "structural-metrics.json"
    semantic_review_file = tmp_path / "multi-llm-semantic-review.json"

    assert json_report_file.is_file()
    assert md_report_file.is_file()
    assert struct_metrics_file.is_file()
    assert semantic_review_file.is_file()

    md_content = md_report_file.read_text(encoding="utf-8")
    assert "Provisional multi-LLM runtime diagnostic: COMPLETE. Official answer-quality benchmark: PENDING HUMAN ADJUDICATION. Authority review: PENDING." in md_content

    for forbidden in ["GOLDEN PASS", "HUMAN APPROVED", "AUTHORITY APPROVED", "LEGALLY VALIDATED"]:
        assert forbidden not in md_content
