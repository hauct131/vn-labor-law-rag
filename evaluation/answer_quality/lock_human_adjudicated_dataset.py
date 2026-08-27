#!/usr/bin/env python3
"""CLI and library to lock a human-adjudicated dataset for technical golden benchmark.

This locks the technical golden set while explicitly preserving
authority_review_status=pending. It cannot be run without human adjudication
or without the explicit --confirm-lock flag.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evaluation.answer_quality.apply_adjudication import (
    _atomic_write,
    _canonical_json,
    _sha256,
)
from evaluation.answer_quality.human_schema import validate_reviewer_name
from evaluation.answer_quality.schema import (
    AnswerQualityDataset,
    AuthorityReviewStatus,
    DatasetStatus,
    LabelStatus,
    load_answer_quality_dataset,
)


DEFAULT_DATASET = Path(
    "data/evaluation/answer-quality-v1/human_adjudicated_questions.json"
)


class DatasetLockError(ValueError):
    """Raised when a dataset cannot be locked for benchmarking."""


def lock_dataset(
    dataset_path: Path,
    *,
    repo_root: Path,
    confirm_lock: bool,
) -> AnswerQualityDataset:
    if not confirm_lock:
        raise DatasetLockError(
            "locking requires explicit confirmation via --confirm-lock"
        )

    dataset = load_answer_quality_dataset(dataset_path)

    if dataset.locked or dataset.dataset_status == DatasetStatus.LOCKED:
        raise DatasetLockError("dataset is already locked")

    if dataset.dataset_status != DatasetStatus.HUMAN_ADJUDICATED:
        raise DatasetLockError(
            f"dataset status must be '{DatasetStatus.HUMAN_ADJUDICATED.value}' before locking"
        )

    if dataset.authority_review_status != AuthorityReviewStatus.PENDING:
        raise DatasetLockError("dataset authority status must be pending")

    corpus_path = repo_root / dataset.source_corpus_path
    if not corpus_path.is_file():
        raise DatasetLockError(
            f"declared canonical corpus missing: {corpus_path}"
        )
    if _sha256(corpus_path) != dataset.source_corpus_sha256:
        raise DatasetLockError("canonical corpus hash mismatch")

    for question in dataset.questions:
        if question.label_status != LabelStatus.HUMAN_ADJUDICATED:
            raise DatasetLockError(
                f"case {question.id}: must have label_status='human_adjudicated'"
            )
        if not question.reviewer.strip():
            raise DatasetLockError(
                f"case {question.id}: reviewer name is missing"
            )
        validate_reviewer_name(question.reviewer)
        if question.authority_review_status != AuthorityReviewStatus.PENDING:
            raise DatasetLockError(
                f"case {question.id}: authority review status must be pending"
            )

    locked_questions: list[dict[str, Any]] = []
    for question in dataset.questions:
        payload = question.model_dump(mode="json")
        payload.update({
            "benchmark_enabled": True,
            "authority_review_status": AuthorityReviewStatus.PENDING.value,
        })
        locked_questions.append(payload)

    try:
        return AnswerQualityDataset.model_validate({
            **dataset.model_dump(mode="json"),
            "dataset_status": DatasetStatus.LOCKED.value,
            "locked": True,
            "authority_review_status": AuthorityReviewStatus.PENDING.value,
            "questions": locked_questions,
        })
    except ValidationError as exc:
        raise DatasetLockError(
            "locked dataset violates answer quality schema"
        ) from exc


def run(
    dataset_path: Path,
    output_path: Path,
    *,
    repo_root: Path,
    confirm_lock: bool,
) -> dict[str, Any]:
    locked_dataset = lock_dataset(
        dataset_path,
        repo_root=repo_root,
        confirm_lock=confirm_lock,
    )
    rendered = _canonical_json(locked_dataset)
    _atomic_write(output_path, rendered)

    return {
        "status": "locked",
        "notice": "Technical answer-quality golden dataset locked pending authority review.",
        "dataset_status": locked_dataset.dataset_status.value,
        "locked": locked_dataset.locked,
        "question_count": len(locked_dataset.questions),
        "benchmark_enabled_count": sum(
            q.benchmark_enabled for q in locked_dataset.questions
        ),
        "authority_review_status": locked_dataset.authority_review_status.value,
        "output": str(output_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lock a human-adjudicated dataset for technical benchmarking."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--confirm-lock",
        action="store_true",
        help="Explicitly confirm locking technical answer-quality golden dataset.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run(
            args.dataset,
            args.output,
            repo_root=args.repo_root,
            confirm_lock=args.confirm_lock,
        )
    except (DatasetLockError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
