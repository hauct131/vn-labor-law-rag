#!/usr/bin/env python3
"""Thin compatibility wrapper delegating to unified runtime verifier engine."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from evaluation.answer_quality.runtime_verification import (
    run_verification,
)

FORBIDDEN_PHRASES = [
    "GOLDEN PASS",
    "HUMAN APPROVED",
    "AUTHORITY APPROVED",
    "LEGALLY VALIDATED",
]


class ProvisionalDiagnosticError(RuntimeError):
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

    corpus_path = (
        repo_root
        / "data/releases/labor-law-canonical-word-20260804-164432-candidate/canonical_chunks.jsonl"
    )

    out_dir = output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    code = run_verification(
        dataset_path=dataset_path,
        corpus_path=corpus_path,
        output_base_dir=out_dir,
        target_url=target_url,
        provisional_diagnostic_mode=provisional_flag,
    )
    if code != 0:
        raise ProvisionalDiagnosticError("Provisional diagnostic run failed.")

    # Locate the final JSON report file (either in out_dir or in run-* subdirectory)
    final_json_files = list(out_dir.glob("**/final-runtime-report.json"))
    if not final_json_files:
        raise ProvisionalDiagnosticError("final-runtime-report.json not found after verification.")

    # Use the most recent final-runtime-report.json
    final_json = max(final_json_files, key=lambda p: p.stat().st_mtime)
    report_data = json.loads(final_json.read_text(encoding="utf-8"))

    # Copy files to top level of out_dir if called via legacy function
    (out_dir / "provisional-runtime-report.json").write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "provisional-runtime-report.md").write_text(
        "# Provisional Multi-LLM Runtime Diagnostic Report\n\n" + str(report_data.get("final_conclusion", "")),
        encoding="utf-8",
    )
    (out_dir / "structural-metrics.json").write_text(
        json.dumps(report_data.get("summary_metrics", {}), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "multi-llm-semantic-review.json").write_text(
        json.dumps({"manifest": report_data.get("manifest", {})}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Fixture detection check: If all cases returned identical answer string, citations, fail.
    cases = report_data.get("cases", [])
    if len(cases) > 1:
        first_sig = (
            cases[0].get("answer"),
            tuple(cases[0].get("cited_article_codes", [])),
            tuple(cases[0].get("cited_chunk_ids", [])),
        )
        if all(
            (
                c.get("answer"),
                tuple(c.get("cited_article_codes", [])),
                tuple(c.get("cited_chunk_ids", [])),
            ) == first_sig
            for c in cases
        ):
            raise ProvisionalDiagnosticError(
                "POSSIBLE_FIXTURE_DATA: all cases returned identical mock/fixture fields. Real runtime verification failed."
            )

    return report_data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run provisional multi-LLM runtime diagnostic evaluation."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/evaluation/answer-quality-v1/multi_llm_reviewed_questions.json"),
    )
    parser.add_argument("--target-url", type=str, default="http://localhost:8000/api")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("~/Downloads/answer-quality-provisional-diagnostic"),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--provisional-multi-llm-diagnostic",
        action="store_true",
        default=False,
        help="Explicitly enable provisional multi-LLM diagnostic mode.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        run_provisional_diagnostic(
            args.dataset,
            args.target_url,
            args.output_dir.expanduser(),
            repo_root=args.repo_root,
            provisional_flag=args.provisional_multi_llm_diagnostic,
        )
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
