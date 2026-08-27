#!/usr/bin/env python3
"""Validate an answer-quality dataset without calling retrieval or an LLM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.answer_quality.schema import (
    DatasetValidationError,
    load_answer_quality_dataset,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate answer/citation evaluation dataset schema and state."
    )
    parser.add_argument("dataset", type=Path)
    return parser


def run(dataset_path: Path) -> dict[str, object]:
    dataset = load_answer_quality_dataset(dataset_path)
    status_counts: dict[str, int] = {}
    for question in dataset.questions:
        key = question.expected_status.value
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "status": "PASS",
        "schema_version": dataset.schema_version,
        "dataset_status": dataset.dataset_status.value,
        "locked": dataset.locked,
        "authority_review_status": dataset.authority_review_status.value,
        "question_count": len(dataset.questions),
        "benchmark_enabled_count": sum(
            question.benchmark_enabled for question in dataset.questions
        ),
        "expected_status_counts": status_counts,
        "source_corpus_release_id": dataset.source_corpus_release_id,
        "source_corpus_sha256": dataset.source_corpus_sha256,
    }


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run(args.dataset)
    except DatasetValidationError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
