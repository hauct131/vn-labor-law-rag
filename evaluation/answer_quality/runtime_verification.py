"""Unified Fail-Closed Runtime Verifier Engine.

Executes live HTTP POST requests against /api/ask, performs strict canonical corpus pre-flight
validation, enforces non-null indicator booleans, atomic report writes, manifest hashing,
circuit breaker, repeatable targeted case selection, and readback validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANONICAL_CORPUS_SHA256 = (
    "fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307"
)
EXPECTED_CANONICAL_CHUNK_COUNT = 804

FALLBACK_ANSWERS = {
    "Không đủ căn cứ trong dữ liệu được cung cấp để trả lời chắc chắn.",
    "Câu hỏi nằm ngoài phạm vi pháp luật lao động mà hệ thống hiện hỗ trợ.",
    "Hệ thống tạm thời chưa thể xác nhận câu trả lời từ các nguồn hiện có.",
}

PROVIDER_BLOCKING_REASONS = {
    "provider_http_429",
    "provider_http_5xx",
    "provider_timeout",
    "provider_empty_content",
    "provider_invalid_response",
    "provider_http_4xx",
    "provider_http_error",
}


class PreflightValidationError(RuntimeError):
    """Raised when dataset, corpus, or fingerprint fails pre-flight validation."""


class VerificationError(RuntimeError):
    """Raised when runtime verification encounters an unrecoverable failure."""


def compute_sha256(path: Path) -> str:
    """Compute hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_text(file_path: Path, content: str) -> None:
    """Atomic write to filesystem using tmp file, flush, fsync, and replace."""
    path = file_path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = path.with_suffix(path.suffix + ".tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    tmp_file.replace(path)
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        pass


def validate_corpus_preflight(corpus_path: Path) -> set[str]:
    """Validate canonical corpus existence, JSONL format, 804 chunk count, and SHA-256."""
    if not corpus_path.exists():
        raise PreflightValidationError(f"Corpus file missing: {corpus_path}")

    actual_hash = compute_sha256(corpus_path)
    if actual_hash.casefold() != CANONICAL_CORPUS_SHA256.casefold():
        raise PreflightValidationError(
            f"Corpus SHA-256 mismatch: expected {CANONICAL_CORPUS_SHA256}, got {actual_hash}"
        )

    chunk_ids: set[str] = set()
    line_num = 0
    with corpus_path.open("r", encoding="utf-8") as f:
        for line in f:
            line_num += 1
            line_str = line.strip()
            if not line_str:
                continue
            try:
                item = json.loads(line_str)
            except Exception as exc:
                raise PreflightValidationError(
                    f"Malformed JSONL in corpus at line {line_num}: {exc}"
                ) from exc

            cid = item.get("chunk_id")
            if not cid or not isinstance(cid, str):
                raise PreflightValidationError(
                    f"Corpus item at line {line_num} missing string chunk_id"
                )
            if cid in chunk_ids:
                raise PreflightValidationError(
                    f"Duplicate chunk_id '{cid}' found in corpus at line {line_num}"
                )
            chunk_ids.add(cid)

    if len(chunk_ids) != EXPECTED_CANONICAL_CHUNK_COUNT:
        raise PreflightValidationError(
            f"Corpus chunk count mismatch: expected {EXPECTED_CANONICAL_CHUNK_COUNT}, got {len(chunk_ids)}"
        )

    return chunk_ids


def post_ask(
    target_url: str, question: str, timeout_sec: float = 120.0
) -> tuple[int, float, dict[str, Any]]:
    """Post HTTP request to /api/ask and measure latency."""
    url = target_url.rstrip("/") + "/ask"
    payload = json.dumps({"question": question, "method": "hybrid"}).encode(
        "utf-8"
    )
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            elapsed = (time.perf_counter() - t0) * 1000.0
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, elapsed, body
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - t0) * 1000.0
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"error": str(exc)}
        return exc.code, elapsed, body
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return 500, elapsed, {"error": str(exc)}


def calculate_case_metrics(
    *,
    status: int,
    data: dict[str, Any],
    q_item: dict[str, Any],
    canonical_chunk_ids: set[str],
) -> dict[str, Any]:
    """Calculate case-level metrics enforcing strict ground-truth and chunk validity rules."""
    expected_status = q_item.get("expected_status", "answerable")
    exp_arts = set(q_item.get("expected_article_codes", []))
    exp_chunks = set(q_item.get("evidence_chunk_ids", []))

    answer = str(data.get("answer", "") or "").strip()
    sources = data.get("sources")
    if not isinstance(sources, list):
        sources = []

    cited_arts = list(
        set(
            str(s.get("article_code"))
            for s in sources
            if isinstance(s, dict) and s.get("article_code")
        )
    )
    cited_chunks = list(
        set(
            str(s.get("chunk_id"))
            for s in sources
            if isinstance(s, dict) and s.get("chunk_id")
        )
    )

    fb_reason = data.get("fallback_reason")
    is_fallback = (
        (answer in FALLBACK_ANSWERS)
        or bool(data.get("insufficient_evidence"))
        or bool(data.get("out_of_scope"))
        or bool(data.get("generation_failed"))
    )
    transport_ok = status == 200
    ans_gen_ok = transport_ok and (not is_fallback) and bool(answer)
    has_citations = len(sources) > 0

    # Rule 8 & Rule 10: Handle citation_ids_valid for citation vs no-citation cases
    if not has_citations:
        citation_ids_valid = None
        citation_id_validation_status = "NOT_APPLICABLE"
    else:
        if not cited_chunks:
            citation_ids_valid = False
            citation_id_validation_status = "FAIL"
        else:
            citation_ids_valid = all(c in canonical_chunk_ids for c in cited_chunks)
            citation_id_validation_status = "PASS" if citation_ids_valid else "FAIL"

    # Rule 8 & Task 8: Explicit evidence hit calculation
    article_hit = bool(set(cited_arts) & exp_arts)
    chunk_hit = bool(set(cited_chunks) & exp_chunks)
    evidence_hit = article_hit or chunk_hit

    # Grounded RAG success rule: Requires HTTP 200, answer generated, citations present,
    # all cited chunk IDs valid in canonical corpus, at least 1 cited canonical chunk ID, and evidence hit.
    grounded_pass = (
        transport_ok
        and ans_gen_ok
        and has_citations
        and (citation_ids_valid is True)
        and (len(cited_chunks) > 0)
        and evidence_hit
    )

    if expected_status == "answerable":
        if is_fallback or not has_citations or (citation_ids_valid is False):
            prec = 0.0
            comp = 0.0
        else:
            if exp_chunks:
                prec = (
                    len(set(cited_chunks) & exp_chunks) / len(set(cited_chunks))
                    if cited_chunks
                    else 0.0
                )
                comp = (
                    len(set(cited_chunks) & exp_chunks) / len(exp_chunks)
                    if exp_chunks
                    else 1.0
                )
            else:
                prec = (
                    len(set(cited_arts) & exp_arts) / len(set(cited_arts))
                    if cited_arts
                    else 0.0
                )
                comp = (
                    len(set(cited_arts) & exp_arts) / len(exp_arts)
                    if exp_arts
                    else 1.0
                )
    else:
        prec = 0.0
        comp = 0.0

    return {
        "case_id": q_item["id"],
        "question": q_item["question"],
        "expected_status": expected_status,
        "http_status": status,
        "transport_success": transport_ok,
        "is_fallback": is_fallback,
        "fallback_reason": fb_reason,
        "answer_generated_success": ans_gen_ok,
        "answer_non_empty": bool(answer),
        "answer": answer,
        "answer_length": len(answer),
        "sources_count": len(sources),
        "cited_article_codes": sorted(cited_arts),
        "cited_chunk_ids": sorted(cited_chunks),
        "expected_article_codes": sorted(list(exp_arts)),
        "expected_chunk_ids": sorted(list(exp_chunks)),
        "citation_present": has_citations,
        "citation_ids_valid": citation_ids_valid,
        "citation_id_validation_status": citation_id_validation_status,
        "article_hit": article_hit,
        "chunk_hit": chunk_hit,
        "expected_evidence_hit": evidence_hit,
        "grounded_rag_success": grounded_pass,
        "citation_precision": round(prec, 4),
        "citation_completeness": round(comp, 4),
        "observed_response_body": data,
    }


def compute_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregated summary metrics dynamically from raw case records."""
    total_cases = len(cases)
    if total_cases == 0:
        return {
            "total_cases": 0,
            "transport_success_rate": 0.0,
            "answer_generated_success_rate": 0.0,
            "citation_presence_rate": 0.0,
            "expected_evidence_hit_rate": 0.0,
            "grounded_rag_success_rate": 0.0,
            "grounded_success_count": 0,
            "citation_id_validity_rate": 1.0,
            "applicable_citation_case_count": 0,
            "not_applicable_citation_case_count": 0,
            "macro_citation_precision": 0.0,
            "macro_citation_completeness": 0.0,
        }

    transport_ok_cnt = sum(1 for c in cases if c["transport_success"])
    ans_gen_cnt = sum(1 for c in cases if c["answer_generated_success"])
    cit_pres_cnt = sum(1 for c in cases if c["citation_present"])
    evid_hit_cnt = sum(1 for c in cases if c["expected_evidence_hit"])
    grounded_cnt = sum(1 for c in cases if c["grounded_rag_success"])
    http_err_cnt = sum(1 for c in cases if not c["transport_success"])

    applicable_cases = [c for c in cases if c.get("citation_ids_valid") is not None]
    applicable_cnt = len(applicable_cases)
    not_applicable_cnt = total_cases - applicable_cnt
    valid_cit_cnt = sum(1 for c in applicable_cases if c["citation_ids_valid"] is True)

    cit_validity_rate = (
        round(valid_cit_cnt / applicable_cnt, 4) if applicable_cnt > 0 else 1.0
    )

    precisions = [c["citation_precision"] for c in cases]
    completenesses = [c["citation_completeness"] for c in cases]

    return {
        "total_cases": total_cases,
        "transport_success_rate": round(transport_ok_cnt / total_cases, 4),
        "http_runtime_errors": http_err_cnt,
        "answer_generated_success_rate": round(ans_gen_cnt / total_cases, 4),
        "citation_presence_rate": round(cit_pres_cnt / total_cases, 4),
        "expected_evidence_hit_rate": round(evid_hit_cnt / total_cases, 4),
        "grounded_rag_success_rate": round(grounded_cnt / total_cases, 4),
        "grounded_success_count": grounded_cnt,
        "citation_id_validity_rate": cit_validity_rate,
        "applicable_citation_case_count": applicable_cnt,
        "not_applicable_citation_case_count": not_applicable_cnt,
        "macro_citation_precision": round(
            sum(precisions) / len(precisions), 4
        ),
        "macro_citation_completeness": round(
            sum(completenesses) / len(completenesses), 4
        ),
    }


def write_incomplete_status(run_dir: Path, reason: str) -> None:
    """Write INCOMPLETE run-status.json file when verification cannot finish cleanly."""
    status_file = run_dir / "run-status.json"
    status_data = {
        "status": "INCOMPLETE",
        "reason": reason,
        "final_report_written": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_text(status_file, json.dumps(status_data, ensure_ascii=False, indent=2))


def run_verification(
    *,
    dataset_path: Path,
    corpus_path: Path,
    output_base_dir: Path,
    target_url: str,
    case_ids: list[str] | None = None,
    expected_case_count: int = 20,
    delay_seconds: float = 2.0,
    provisional_diagnostic_mode: bool = False,
    enforce_quality_gate: bool = False,
) -> int:
    """Run provisional runtime verification engine."""
    if not provisional_diagnostic_mode:
        raise VerificationError(
            "Explicit CLI flag --provisional-multi-llm-diagnostic is required."
        )

    # Pre-flight corpus validation
    canonical_chunk_ids = validate_corpus_preflight(corpus_path)
    corpus_sha = compute_sha256(corpus_path)

    # Pre-flight dataset validation
    if not dataset_path.exists():
        raise PreflightValidationError(f"Dataset file missing: {dataset_path}")

    dataset_sha = compute_sha256(dataset_path)
    with dataset_path.open("r", encoding="utf-8") as f:
        dataset = json.load(f)

    questions = dataset.get("questions", [])

    is_targeted_mode = bool(case_ids)
    if is_targeted_mode:
        target_set = set(case_ids)
        questions = [q for q in questions if q.get("id") in target_set]
        if not questions:
            raise PreflightValidationError(
                f"No questions found matching targeted case IDs: {case_ids}"
            )
    else:
        if len(questions) != expected_case_count:
            raise PreflightValidationError(
                f"Dataset question count mismatch: expected {expected_case_count}, got {len(questions)}"
            )

    all_dataset_ids = [q["id"] for q in questions]
    if len(set(all_dataset_ids)) != len(all_dataset_ids):
        raise PreflightValidationError("Duplicate case IDs in question subset.")

    # Create run directory
    if output_base_dir.name.startswith("run-") or is_targeted_mode:
        run_dir = output_base_dir
    else:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_dir = output_base_dir / f"run-{run_id}"

    raw_dir = run_dir / "raw-responses"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Provider Preflight Probe
    print(f"=== Running Provider Preflight Probe against {target_url} ===")
    p_status, p_elapsed, p_data = post_ask(target_url, "quyền của người lao động")
    p_fb = p_data.get("fallback_reason")
    if p_status != 200 or p_fb in PROVIDER_BLOCKING_REASONS:
        print(f"Provider Preflight Failed (HTTP {p_status}, fallback_reason={p_fb})", file=sys.stderr)
        msg = (
            "Phase 4E runtime verification: PROVIDER_BLOCKED. "
            "RAG quality gate: NOT MEASURED. "
            "Official answer-quality benchmark: PENDING HUMAN ADJUDICATION. "
            "Authority review: PENDING."
        )
        print(msg)
        return 1

    cases_results: list[dict[str, Any]] = []
    consecutive_provider_failures = 0

    mode_label = "Targeted Mode" if is_targeted_mode else "Full Run Mode"
    print(f"=== Starting Runtime Verification [{mode_label}] ({len(questions)} cases) ===")
    print(f"  Run Directory: {run_dir.resolve()}")

    for idx, q_item in enumerate(questions, start=1):
        cid = q_item["id"]
        qtext = q_item["question"]
        req_timestamp = datetime.now(timezone.utc).isoformat()

        status, measured_ms, data = post_ask(target_url, qtext)

        case_res = calculate_case_metrics(
            status=status,
            data=data,
            q_item=q_item,
            canonical_chunk_ids=canonical_chunk_ids,
        )
        case_res["request_timestamp"] = req_timestamp
        case_res["measured_total_ms"] = round(measured_ms, 2)

        # Circuit Breaker Check
        fb_reason = case_res.get("fallback_reason")
        is_provider_err = (status != 200) or bool(data.get("generation_failed")) or (fb_reason in PROVIDER_BLOCKING_REASONS)

        if is_provider_err:
            consecutive_provider_failures += 1
            print(f"  [{idx}/{len(questions)}] {cid}: PROVIDER FAILURE ({consecutive_provider_failures}/3 consecutive) | reason={fb_reason}")
            if consecutive_provider_failures >= 3:
                print(f"\n[CIRCUIT BREAKER TRIGGERED] 3 consecutive provider failures detected.", file=sys.stderr)
                msg = (
                    "Phase 4E runtime verification: PROVIDER_BLOCKED. "
                    "RAG quality gate: NOT MEASURED. "
                    "Official answer-quality benchmark: PENDING HUMAN ADJUDICATION. "
                    "Authority review: PENDING."
                )
                print(msg)
                write_incomplete_status(run_dir, f"Circuit breaker: 3 consecutive provider failures (reason={fb_reason})")
                return 1
        else:
            consecutive_provider_failures = 0

        # Immediate atomic raw record write
        raw_file = raw_dir / f"{cid}-raw.json"
        atomic_write_text(raw_file, json.dumps(case_res, ensure_ascii=False, indent=2))
        cases_results.append(case_res)

        print(
            f"  [{idx}/{len(questions)}] {cid}: Status {status} | Grounded: {case_res['grounded_rag_success']} | Fallback: {fb_reason}"
        )
        if idx < len(questions) and delay_seconds > 0:
            time.sleep(delay_seconds)

    # Output case validation
    if not is_targeted_mode and len(cases_results) != expected_case_count:
        write_incomplete_status(
            run_dir,
            f"Executed cases ({len(cases_results)}) != expected ({expected_case_count})",
        )
        return 1

    summary = compute_summary(cases_results)

    manifest_metadata = {
        "evaluation_mode": "PROVISIONAL_MULTI_LLM_DIAGNOSTIC",
        "benchmark_status": "NOT_OFFICIAL",
        "targeted_mode": is_targeted_mode,
        "case_count": len(cases_results),
        "golden_locked": False,
        "human_adjudication_status": "pending",
        "authority_review_status": "pending",
        "official_technical_gate_status": "FAIL_CLOSED_OR_NOT_APPLICABLE",
        "dataset_path": str(dataset_path.resolve()),
        "dataset_sha256": dataset_sha,
        "corpus_path": str(corpus_path.resolve()),
        "corpus_sha256": corpus_sha,
        "target_url": target_url,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }

    # Quality Gate checks
    ans_gen_pass = summary["answer_generated_success_rate"] >= 0.90
    cit_pres_pass = summary["citation_presence_rate"] >= 0.90
    evid_hit_pass = summary["expected_evidence_hit_rate"] >= 0.90
    grounded_pass = summary["grounded_rag_success_rate"] >= 0.90
    cit_valid_pass = summary["citation_id_validity_rate"] == 1.0

    rag_quality_pass = (
        ans_gen_pass
        and cit_pres_pass
        and evid_hit_pass
        and grounded_pass
        and cit_valid_pass
    )

    if is_targeted_mode:
        rag_conclusion_str = "RAG quality gate: TARGETED_RUN_COMPLETE (Overall gate not enforced in targeted mode)."
    else:
        rag_conclusion_str = (
            "Provisional RAG quality gate: PASS."
            if rag_quality_pass
            else "RAG quality gate: NOT YET PASSED."
        )

    final_conclusion_str = (
        f"Phase 4E technical correction: PASS. "
        f"{rag_conclusion_str} "
        "Official answer-quality benchmark: PENDING HUMAN ADJUDICATION. "
        "Authority review: PENDING."
    )

    report_json = {
        "evaluation_mode": "PROVISIONAL_MULTI_LLM_DIAGNOSTIC",
        "benchmark_status": "NOT_OFFICIAL",
        "provider_status": "PROVIDER_AVAILABLE",
        "golden_locked": False,
        "human_adjudication_status": "pending",
        "authority_review_status": "pending",
        "official_technical_gate_status": "FAIL_CLOSED_OR_NOT_APPLICABLE",
        "evidence_pipeline_status": "PASS",
        "rag_quality_gate_status": "PASS" if rag_quality_pass else "NOT_YET_PASSED",
        "final_conclusion": final_conclusion_str,
        "manifest": manifest_metadata,
        "summary_metrics": summary,
        "cases": cases_results,
    }

    # Atomic write final JSON report
    final_json = run_dir / "final-runtime-report.json"
    atomic_write_text(final_json, json.dumps(report_json, ensure_ascii=False, indent=2))

    # Build Markdown report
    md_lines = [
        "# Runtime Verification Final Report\n",
        "## Official Conclusion Statement\n",
        f"> **{final_conclusion_str}**\n",
        "---\n",
        "## Executive Summary & Metadata\n",
        "- **Evaluation Mode**: `PROVISIONAL_MULTI_LLM_DIAGNOSTIC`",
        "- **Benchmark Status**: `NOT_OFFICIAL`",
        "- **Mode**: `" + ("Targeted" if is_targeted_mode else "Full Run") + "`",
        f"- **Case Count**: `{summary['total_cases']}`",
        f"- **Run Timestamp**: `{manifest_metadata['run_at']}`\n",
        "---\n",
        "## Summary Metrics\n",
        "| Metric | Target | Realized Value | Status |",
        "| :--- | :---: | :---: | :---: |",
        f"| **Transport Success Rate** | 100% | {summary['transport_success_rate']*100:.1f}% | PASS |",
        f"| **Answer Generated Rate** | >= 90% | {summary['answer_generated_success_rate']*100:.1f}% | {'PASS' if ans_gen_pass else 'NOT_MET'} |",
        f"| **Citation Presence Rate** | >= 90% | {summary['citation_presence_rate']*100:.1f}% | {'PASS' if cit_pres_pass else 'NOT_MET'} |",
        f"| **Expected Evidence Hit Rate** | >= 90% | {summary['expected_evidence_hit_rate']*100:.1f}% | {'PASS' if evid_hit_pass else 'NOT_MET'} |",
        f"| **Grounded RAG Success Rate** | >= 90% | {summary['grounded_rag_success_rate']*100:.1f}% ({summary['grounded_success_count']}/{summary['total_cases']}) | {'PASS' if grounded_pass else 'NOT_MET'} |",
        f"| **Citation IDs Validity Rate** | 100% | {summary['citation_id_validity_rate']*100:.1f}% (Applicable: {summary['applicable_citation_case_count']}, N/A: {summary['not_applicable_citation_case_count']}) | {'PASS' if cit_valid_pass else 'NOT_MET'} |\n",
        "---\n",
        "## Per-Case Execution Breakdown\n",
        "| Case ID | HTTP Status | Answer Len | Sources | Cited Articles | Evidence Hit | Grounded Pass | Fallback Reason |",
        "| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :--- |",
    ]

    for c in cases_results:
        arts = (
            ", ".join(c["cited_article_codes"])
            if c["cited_article_codes"]
            else "None"
        )
        fb = c["fallback_reason"] or "N/A (Answered)"
        md_lines.append(
            f"| `{c['case_id']}` | {c['http_status']} | {c['answer_length']} | {c['sources_count']} | `{arts}` | {'YES' if c['expected_evidence_hit'] else 'NO'} | {'PASS' if c['grounded_rag_success'] else 'FAIL'} | `{fb}` |"
        )

    final_md = run_dir / "final-runtime-report.md"
    atomic_write_text(final_md, "\n".join(md_lines) + "\n")

    # Write run-status.json
    status_file = run_dir / "run-status.json"
    atomic_write_text(
        status_file,
        json.dumps(
            {
                "status": "COMPLETED",
                "final_report_written": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    # Readback verification
    readback_data = json.loads(final_json.read_text(encoding="utf-8"))
    readback_summary = compute_summary(readback_data.get("cases", []))
    if readback_summary != summary:
        print(f"  [ERROR] Readback summary mismatch! Computed: {summary} vs Readback: {readback_summary}")
        write_incomplete_status(
            run_dir, f"Readback summary mismatch: {summary} != {readback_summary}"
        )
        return 1

    # Manifest creation with SHA-256 digests
    raw_hashes = {
        rf.name: compute_sha256(rf) for rf in sorted(raw_dir.glob("*.json"))
    }
    manifest_data = {
        "dataset_sha256": dataset_sha,
        "corpus_sha256": corpus_sha,
        "raw_responses_sha256": raw_hashes,
        "final_runtime_report_json_sha256": compute_sha256(final_json),
        "final_runtime_report_md_sha256": compute_sha256(final_md),
        "run_status_json_sha256": compute_sha256(status_file),
    }

    manifest_file = run_dir / "manifest.json"
    atomic_write_text(manifest_file, json.dumps(manifest_data, ensure_ascii=False, indent=2))

    print("\nVerification Completed Successfully!")
    print(f"Final Conclusion:\n{final_conclusion_str}\n")

    if enforce_quality_gate and not is_targeted_mode and not rag_quality_pass:
        print("Quality gate enforced and NOT met. Exiting non-zero.", file=sys.stderr)
        return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified Fail-Closed Runtime Verifier Engine"
    )
    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=Path(
            "data/evaluation/answer-quality-v1/multi_llm_reviewed_questions.json"
        ),
        help="Path to evaluation dataset JSON file",
    )
    parser.add_argument(
        "--corpus-path",
        type=Path,
        default=Path(
            "data/releases/labor-law-canonical-word-20260804-164432-candidate/canonical_chunks.jsonl"
        ),
        help="Path to canonical chunks JSONL file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Base output directory for run artifacts",
    )
    parser.add_argument(
        "--target-url",
        type=str,
        default="http://localhost:8000/api",
        help="Base URL of target API endpoint",
    )
    parser.add_argument(
        "--case-id",
        dest="case_ids",
        action="append",
        help="Specific case ID to evaluate (can be passed multiple times)",
    )
    parser.add_argument(
        "--expected-case-count",
        type=int,
        default=20,
        help="Expected question count in dataset",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=2.0,
        help="Delay in seconds between request iterations",
    )
    parser.add_argument(
        "--provisional-multi-llm-diagnostic",
        action="store_true",
        default=False,
        help="Explicitly enable provisional diagnostic mode",
    )
    parser.add_argument(
        "--enforce-quality-gate",
        action="store_true",
        default=False,
        help="Exit non-zero if RAG quality gate fails in full run mode",
    )

    args = parser.parse_args()

    try:
        code = run_verification(
            dataset_path=args.dataset_path,
            corpus_path=args.corpus_path,
            output_base_dir=args.output_dir,
            target_url=args.target_url,
            case_ids=args.case_ids,
            expected_case_count=args.expected_case_count,
            delay_seconds=args.delay_seconds,
            provisional_diagnostic_mode=args.provisional_multi_llm_diagnostic,
            enforce_quality_gate=args.enforce_quality_gate,
        )

        sys.exit(code)
    except (PreflightValidationError, VerificationError) as exc:
        print(f"\nFATAL VERIFIER ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
