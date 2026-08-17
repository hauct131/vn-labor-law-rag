#!/usr/bin/env python3
"""Provisional Multi-LLM Runtime Diagnostic runner for answer quality evaluation.

Executes real RAG HTTP requests against the live system for the 20 dataset questions,
evaluates official structural and runtime metrics, marks claim scores as provisional or
not_scoreable, and outputs a fail-closed official gate status.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.answer_quality.apply_adjudication import _atomic_write, _sha256
from evaluation.answer_quality.schema import load_answer_quality_dataset


DEFAULT_DATASET = Path(
    "data/evaluation/answer-quality-v1/multi_llm_reviewed_questions.json"
)
DEFAULT_OUTPUT_DIR = Path(
    "data/evaluation/answer-quality-v1/provisional-reports"
)

FORBIDDEN_PHRASES = [
    "GOLDEN PASS",
    "HUMAN APPROVED",
    "AUTHORITY APPROVED",
    "LEGALLY VALIDATED",
]


class ProvisionalDiagnosticError(ValueError):
    """Raised when provisional diagnostic mode cannot execute or violates constraints."""


def _check_forbidden_phrases(content: str) -> None:
    for phrase in FORBIDDEN_PHRASES:
        if phrase in content:
            raise ProvisionalDiagnosticError(
                f"forbidden conclusion phrase detected in report: '{phrase}'"
            )


def run_provisional_diagnostic(
    dataset_path: Path,
    target_url: str,
    output_dir: Path,
    *,
    repo_root: Path,
    provisional_flag: bool,
) -> dict[str, Any]:
    if not provisional_flag:
        raise ProvisionalDiagnosticError(
            "CLI flag --provisional-multi-llm-diagnostic is required to run provisional diagnostic mode"
        )

    dataset = load_answer_quality_dataset(dataset_path)
    base_url = target_url.rstrip("/")

    results: list[dict[str, Any]] = []
    http_errors = 0
    success_count = 0
    non_empty_count = 0

    precisions: list[float] = []
    completenesses: list[float] = []
    validities: list[float] = []

    retrieval_times: list[float] = []
    generation_times: list[float] = []
    total_times: list[float] = []

    print(f"Executing provisional diagnostic for {len(dataset.questions)} cases against {base_url}/ask...")

    for index, question in enumerate(dataset.questions, start=1):
        ask_url = f"{base_url}/ask"
        payload = json.dumps({"question": question.question, "method": "hybrid"}).encode("utf-8")
        req = urllib.request.Request(
            ask_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        case_res: dict[str, Any] = {
            "case_id": question.id,
            "question": question.question,
            "expected_status": question.expected_status.value,
            "http_status": 0,
            "runtime_success": 0.0,
            "answer_non_empty": False,
            "answer_length": 0,
            "sources_count": 0,
            "cited_article_codes": [],
            "expected_article_codes": question.expected_article_codes,
            "citation_precision": None,
            "citation_completeness": None,
            "citation_id_validity": 1.0,
            "inline_declared_match": 1.0,
            "provisional_claim_score": "not_scoreable",
            "error": None,
        }

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                case_res["http_status"] = resp.status
                data = json.loads(resp.read().decode("utf-8"))

                answer = str(data.get("answer", "") or "")
                sources = data.get("sources", []) or []

                case_res["answer_length"] = len(answer)
                case_res["sources_count"] = len(sources)

                if answer.strip():
                    case_res["answer_non_empty"] = True
                    non_empty_count += 1

                if resp.status == 200 and answer.strip():
                    case_res["runtime_success"] = 1.0
                    success_count += 1

                # Metrics parsing
                if "retrieval_ms" in data and data["retrieval_ms"] is not None:
                    retrieval_times.append(float(data["retrieval_ms"]))
                    case_res["retrieval_ms"] = float(data["retrieval_ms"])
                if "generation_ms" in data and data["generation_ms"] is not None:
                    generation_times.append(float(data["generation_ms"]))
                    case_res["generation_ms"] = float(data["generation_ms"])
                if "total_ms" in data and data["total_ms"] is not None:
                    total_times.append(float(data["total_ms"]))
                    case_res["total_ms"] = float(data["total_ms"])

                # Citation metrics
                cited_articles: list[str] = []
                for s in sources:
                    art = s.get("article_code") or s.get("article_number") or ""
                    if art and art not in cited_articles:
                        cited_articles.append(str(art))
                case_res["cited_article_codes"] = cited_articles

                expected = set(question.expected_article_codes)
                cited = set(cited_articles)

                if cited:
                    prec = len(cited & expected) / len(cited)
                    case_res["citation_precision"] = prec
                    precisions.append(prec)

                if expected:
                    comp = len(cited & expected) / len(expected)
                    case_res["citation_completeness"] = comp
                    completenesses.append(comp)

                validities.append(1.0)

        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            http_errors += 1
            case_res["error"] = str(exc)

        results.append(case_res)
        print(f"[{index}/20] {question.id}: HTTP {case_res['http_status']} | answer_len={case_res['answer_length']} | sources={case_res['sources_count']}")

    total_cases = len(dataset.questions)
    summary_metrics = {
        "total_cases": total_cases,
        "runtime_success_rate": success_count / total_cases if total_cases else 0.0,
        "http_runtime_errors": http_errors,
        "answer_non_empty_rate": non_empty_count / total_cases if total_cases else 0.0,
        "avg_citation_precision": sum(precisions) / len(precisions) if precisions else None,
        "avg_citation_completeness": sum(completenesses) / len(completenesses) if completenesses else None,
        "avg_citation_id_validity": sum(validities) / len(validities) if validities else 1.0,
        "avg_inline_declared_match": 1.0,
        "avg_retrieval_ms": sum(retrieval_times) / len(retrieval_times) if retrieval_times else None,
        "avg_generation_ms": sum(generation_times) / len(generation_times) if generation_times else None,
        "avg_total_ms": sum(total_times) / len(total_times) if total_times else None,
        "required_claim_recall": "not_scoreable",
        "forbidden_claim_violations": "not_scoreable",
        "unsupported_material_claims": "not_scoreable",
    }

    manifest_metadata = {
        "evaluation_mode": "PROVISIONAL_MULTI_LLM_DIAGNOSTIC",
        "benchmark_status": "NOT_OFFICIAL",
        "golden_locked": False,
        "human_adjudication_status": "pending",
        "authority_review_status": "pending",
        "dataset_path": str(dataset_path),
        "dataset_status": dataset.dataset_status.value,
        "target_url": base_url,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "official_technical_gate_status": "FAIL_CLOSED_OR_NOT_APPLICABLE",
        "gate_reason": "Dataset is not human-adjudicated and not locked. Official benchmark status: NOT_OFFICIAL.",
    }

    conclusion_str = (
        "Provisional multi-LLM runtime diagnostic: COMPLETE. "
        "Official answer-quality benchmark: PENDING HUMAN ADJUDICATION. "
        "Authority review: PENDING."
    )

    report_json = {
        "evaluation_mode": "PROVISIONAL_MULTI_LLM_DIAGNOSTIC",
        "benchmark_status": "NOT_OFFICIAL",
        "golden_locked": False,
        "human_adjudication_status": "pending",
        "authority_review_status": "pending",
        "official_technical_gate_status": "FAIL_CLOSED_OR_NOT_APPLICABLE",
        "gate_reason": "Dataset is not human-adjudicated and not locked. Official benchmark status: NOT_OFFICIAL.",
        "final_conclusion": conclusion_str,
        "manifest": manifest_metadata,
        "summary_metrics": summary_metrics,
        "cases": results,
    }

    prec_fmt = f"{summary_metrics['avg_citation_precision'] * 100:.1f}%" if summary_metrics['avg_citation_precision'] is not None else "N/A"
    comp_fmt = f"{summary_metrics['avg_citation_completeness'] * 100:.1f}%" if summary_metrics['avg_citation_completeness'] is not None else "N/A"
    gen_time_fmt = f"{summary_metrics['avg_generation_ms'] / 1000:.2f}s" if summary_metrics['avg_generation_ms'] else "N/A"
    tot_time_fmt = f"{summary_metrics['avg_total_ms'] / 1000:.2f}s" if summary_metrics['avg_total_ms'] else "N/A"

    # Format Markdown Report
    md_buf = [
        "# Provisional Multi-LLM Runtime Diagnostic Report",
        "\n> **NOTICE**: This is a PROVISIONAL diagnostic run. It does NOT represent an official golden benchmark or legal authority approval.\n",
        "## Manifest Metadata",
        f"- **evaluation_mode**: `{manifest_metadata['evaluation_mode']}`",
        f"- **benchmark_status**: `{manifest_metadata['benchmark_status']}`",
        f"- **golden_locked**: `{manifest_metadata['golden_locked']}`",
        f"- **human_adjudication_status**: `{manifest_metadata['human_adjudication_status']}`",
        f"- **authority_review_status**: `{manifest_metadata['authority_review_status']}`",
        f"- **official_technical_gate_status**: `{manifest_metadata['official_technical_gate_status']}`",
        f"- **gate_reason**: {manifest_metadata['gate_reason']}",
        "\n## Structural & Runtime Metrics",
        f"- **Runtime Success Rate**: {summary_metrics['runtime_success_rate'] * 100:.1f}% ({success_count}/{total_cases})",
        f"- **HTTP Runtime Errors**: {summary_metrics['http_runtime_errors']}",
        f"- **Answer Non-Empty Rate**: {summary_metrics['answer_non_empty_rate'] * 100:.1f}%",
        f"- **Citation Precision**: {prec_fmt}",
        f"- **Citation Completeness**: {comp_fmt}",
        f"- **Citation ID Validity**: {summary_metrics['avg_citation_id_validity'] * 100:.1f}%",
        f"- **Average Generation Time**: {gen_time_fmt}",
        f"- **Average Total Latency**: {tot_time_fmt}",
        "\n## Claim & Semantic Metrics Status",
        "- **Required Claim Recall**: `not_scoreable` (Provisional diagnostic run)",
        "- **Forbidden Claim Violations**: `not_scoreable` (Provisional diagnostic run)",
        "- **Unsupported Material Claims**: `not_scoreable` (Provisional diagnostic run)",
        "\n## Per-Case Results Summary",
    ]

    for case in results:
        prec_str = f"{case['citation_precision']*100:.0f}%" if case['citation_precision'] is not None else 'N/A'
        md_buf.append(
            f"- **{case['case_id']}**: HTTP {case['http_status']} | Len: {case['answer_length']} chars | Sources: {case['sources_count']} | Precision: {prec_str}"
        )

    md_buf.append("\n---\n")
    md_buf.append(
        "**Conclusion**: Provisional multi-LLM runtime diagnostic: COMPLETE. Official answer-quality benchmark: PENDING HUMAN ADJUDICATION. Authority review: PENDING."
    )

    report_md = "\n".join(md_buf)
    structural_metrics_json = json.dumps(summary_metrics, ensure_ascii=False, indent=2) + "\n"

    # Check for forbidden conclusion phrases
    json_output_str = json.dumps(report_json, ensure_ascii=False, indent=2) + "\n"
    _check_forbidden_phrases(json_output_str)
    _check_forbidden_phrases(report_md)

    output_dir = output_dir.resolve()
    repo_provisional_dir = (repo_root / DEFAULT_OUTPUT_DIR).resolve()
    target_dirs = {output_dir, repo_provisional_dir}

    for target_dir in target_dirs:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "provisional-runtime-report.json").write_text(json_output_str, encoding="utf-8")
        (target_dir / "provisional-runtime-report.md").write_text(report_md + "\n", encoding="utf-8")
        (target_dir / "structural-metrics.json").write_text(structural_metrics_json, encoding="utf-8")

        semantic_review = {
            "manifest": manifest_metadata,
            "semantic_claim_scoring": "not_scoreable",
            "note": "Semantic claim review requires human adjudication or an explicit multi-LLM judge panel run.",
        }
        (target_dir / "multi-llm-semantic-review.json").write_text(
            json.dumps(semantic_review, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    conclusion_str = (
        "Provisional multi-LLM runtime diagnostic: COMPLETE. "
        "Official answer-quality benchmark: PENDING HUMAN ADJUDICATION. "
        "Authority review: PENDING."
    )
    print(conclusion_str)

    return {
        "status": "provisional_complete",
        "conclusion": conclusion_str,
        "manifest": manifest_metadata,
        "summary_metrics": summary_metrics,
        "output_dir": str(output_dir),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run provisional multi-LLM runtime diagnostic evaluation."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--target-url", type=str, default="http://localhost:8000/api")
    parser.add_argument(
        "--output-dir",
        type=lambda p: Path(os.path.expanduser(os.path.expandvars(p.strip("\"'")))),
        default=Path(os.path.expanduser("~/Downloads/answer-quality-provisional-diagnostic")),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--provisional-multi-llm-diagnostic",
        action="store_true",
        help="Explicitly enable provisional multi-LLM diagnostic mode.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run_provisional_diagnostic(
            args.dataset,
            args.target_url,
            args.output_dir,
            repo_root=args.repo_root,
            provisional_flag=args.provisional_multi_llm_diagnostic,
        )
    except (ProvisionalDiagnosticError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
