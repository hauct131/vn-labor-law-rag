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


def test_provisional_diagnostic_detects_possible_fixture_data(tmp_path: Path) -> None:
    # Mock static identical responses -> must raise POSSIBLE_FIXTURE_DATA
    mock_response_body = json.dumps({
        "answer": "Identical mock answer string...",
        "sources": [{"chunk_id": "c1", "article_code": "20.2.LQ.35"}],
        "total_ms": 1000.0,
    }).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = mock_response_body
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(ProvisionalDiagnosticError, match="POSSIBLE_FIXTURE_DATA"):
            run_provisional_diagnostic(
                REPO_ROOT / DEFAULT_DATASET,
                "http://localhost:8000/api",
                tmp_path,
                repo_root=REPO_ROOT,
                provisional_flag=True,
            )


def test_provisional_diagnostic_execution_and_reports(tmp_path: Path) -> None:
    # Varying mock responses per question index to simulate dynamic API returns
    call_count = 0

    def dynamic_urlopen(req, timeout=90):
        nonlocal call_count
        call_count += 1
        body = json.dumps({
            "answer": f"Answer content for case {call_count} with length {100 + call_count}",
            "sources": [
                {
                    "chunk_id": f"chunk_id_{call_count}",
                    "article_code": f"20.2.LQ.{call_count}",
                }
            ],
            "retrieval_ms": 10.0 + call_count,
            "generation_ms": 500.0 + call_count * 10,
            "total_ms": 510.0 + call_count * 11,
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = body
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=dynamic_urlopen):
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
    assert summary["transport_success_rate"] == 1.0
    assert summary["http_runtime_errors"] == 0

    # Check generated files in tmp_path
    assert (tmp_path / "provisional-runtime-report.json").is_file()
    assert (tmp_path / "provisional-runtime-report.md").is_file()
    assert (tmp_path / "structural-metrics.json").is_file()
    assert (tmp_path / "multi-llm-semantic-review.json").is_file()
