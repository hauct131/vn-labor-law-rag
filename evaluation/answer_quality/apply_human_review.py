#!/usr/bin/env python3
"""Apply human adjudication decisions to produce a human-adjudicated dataset.

Enforces fail-closed rules:
- Fails if any case is rejected or undecided.
- Fails if reviewer is blank or an AI/model name.
- Validates edited claims against citation allowlists and canonical corpus.
- Sets dataset_status=human_adjudicated while keeping locked=false,
  benchmark_enabled=false, and authority_review_status=pending.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evaluation.answer_quality.apply_adjudication import (
    _atomic_write,
    _canonical_json,
    _load_chunk_index,
    _sha256,
)
from evaluation.answer_quality.human_schema import (
    HumanAdjudicationArtifact,
    validate_reviewer_name,
)
from evaluation.answer_quality.schema import (
    AnswerQualityDataset,
    AuthorityReviewStatus,
    DatasetStatus,
    LabelStatus,
    load_answer_quality_dataset,
)


DEFAULT_DATASET = Path(
    "data/evaluation/answer-quality-v1/multi_llm_reviewed_questions.json"
)
DEFAULT_REVIEW = Path(
    os.path.expanduser("~/Downloads/answer-quality-human-review/human-review.json")
)
DEFAULT_OUTPUT = Path(
    "data/evaluation/answer-quality-v1/human_adjudicated_questions.json"
)


class HumanReviewApplicationError(ValueError):
    """Raised when human review decisions cannot be applied."""


def _load_review_artifact(path: Path) -> HumanAdjudicationArtifact:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return HumanAdjudicationArtifact.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise HumanReviewApplicationError(
            f"invalid human adjudication artifact: {path}"
        ) from exc


def build_human_adjudicated_dataset(
    dataset_path: Path,
    review_path: Path,
    *,
    repo_root: Path,
) -> AnswerQualityDataset:
    source = load_answer_quality_dataset(dataset_path)
    review_artifact = _load_review_artifact(review_path)

    if source.locked:
        raise HumanReviewApplicationError(
            "input dataset must be an unlocked dataset"
        )
    if source.authority_review_status != AuthorityReviewStatus.PENDING:
        raise HumanReviewApplicationError(
            "source authority status must be pending"
        )
    if any(question.benchmark_enabled for question in source.questions):
        raise HumanReviewApplicationError(
            "source questions must not have benchmark_enabled=true"
        )

    corpus_path = repo_root / source.source_corpus_path
    if not corpus_path.is_file():
        raise HumanReviewApplicationError(
            f"declared canonical corpus missing: {corpus_path}"
        )
    if _sha256(corpus_path) != source.source_corpus_sha256:
        raise HumanReviewApplicationError("canonical corpus hash mismatch")
    chunk_index = _load_chunk_index(corpus_path)

    # Validate reviewer name
    reviewer = validate_reviewer_name(review_artifact.reviewer)

    source_ids = [q.id for q in source.questions]
    review_by_id = {c.case_id: c for c in review_artifact.cases}

    missing = [q_id for q_id in source_ids if q_id not in review_by_id]
    extra = [c_id for c_id in review_by_id if c_id not in source_ids]
    if missing or extra:
        raise HumanReviewApplicationError(
            f"human review coverage mismatch: missing={missing}, extra={extra}"
        )

    # Fail closed check: ALL cases must be explicitly 'accept' or 'edit'
    rejected_or_undecided = [
        case.case_id
        for case in review_artifact.cases
        if case.decision not in ("accept", "edit")
    ]
    if rejected_or_undecided:
        raise HumanReviewApplicationError(
            f"review failed closed: rejected or undecided cases found: {rejected_or_undecided}"
        )

    adjudicated_questions: list[dict[str, Any]] = []

    for question in source.questions:
        case = review_by_id[question.id]
        allowed_articles = set(question.expected_article_codes)
        allowed_chunks = set(question.evidence_chunk_ids)

        if case.decision == "accept":
            required_claims = question.required_claims
            forbidden_claims = question.forbidden_claims
        elif case.decision == "edit":
            required_claims = (
                case.edited_required_claims
                if case.edited_required_claims
                else question.required_claims
            )
            forbidden_claims = (
                case.edited_forbidden_claims
                if case.edited_forbidden_claims
                else question.forbidden_claims
            )

        # Validate claims against allowlists and corpus
        for claim in required_claims:
            if not set(claim.supported_by_article_codes).issubset(
                allowed_articles
            ):
                raise HumanReviewApplicationError(
                    f"{question.id}/{claim.claim_id}: article outside allowlist"
                )
            if not set(claim.supported_by_chunk_ids).issubset(allowed_chunks):
                raise HumanReviewApplicationError(
                    f"{question.id}/{claim.claim_id}: chunk outside allowlist"
                )
            for chunk_id in claim.supported_by_chunk_ids:
                chunk = chunk_index.get(chunk_id)
                if chunk is None:
                    raise HumanReviewApplicationError(
                        f"{question.id}/{claim.claim_id}: chunk missing from corpus"
                    )
                if chunk.get("article_code") not in claim.supported_by_article_codes:
                    raise HumanReviewApplicationError(
                        f"{question.id}/{claim.claim_id}: chunk/article code mismatch"
                    )

        payload = question.model_dump(mode="json")
        payload.update({
            "required_claims": [
                c.model_dump(mode="json") for c in required_claims
            ],
            "forbidden_claims": [
                c.model_dump(mode="json") for c in forbidden_claims
            ],
            "label_status": LabelStatus.HUMAN_ADJUDICATED.value,
            "authority_review_status": AuthorityReviewStatus.PENDING.value,
            "reviewer": reviewer,
            "review_notes": (
                f"Human adjudicated (decision={case.decision}). "
                f"{case.reviewer_notes}".strip()
            ),
            "benchmark_enabled": False,  # MUST NOT auto-enable benchmark
        })
        adjudicated_questions.append(payload)

    try:
        return AnswerQualityDataset.model_validate({
            **source.model_dump(mode="json"),
            "dataset_status": DatasetStatus.HUMAN_ADJUDICATED.value,
            "locked": False,
            "authority_review_status": AuthorityReviewStatus.PENDING.value,
            "questions": adjudicated_questions,
        })
    except ValidationError as exc:
        raise HumanReviewApplicationError(
            "human adjudicated dataset violates schema"
        ) from exc


def run(
    dataset_path: Path,
    review_path: Path,
    output_path: Path,
    *,
    repo_root: Path,
    check: bool,
) -> dict[str, Any]:
    dataset = build_human_adjudicated_dataset(
        dataset_path,
        review_path,
        repo_root=repo_root,
    )
    rendered = _canonical_json(dataset)

    if check:
        if not output_path.is_file():
            raise HumanReviewApplicationError(f"output is missing: {output_path}")
        if output_path.read_text(encoding="utf-8") != rendered:
            raise HumanReviewApplicationError(f"output is stale: {output_path}")
        status = "check_pass"
    else:
        _atomic_write(output_path, rendered)
        status = "written"

    return {
        "status": status,
        "dataset_status": dataset.dataset_status.value,
        "locked": dataset.locked,
        "question_count": len(dataset.questions),
        "reviewer": dataset.questions[0].reviewer if dataset.questions else "",
        "benchmark_enabled_count": sum(
            q.benchmark_enabled for q in dataset.questions
        ),
        "authority_review_status": dataset.authority_review_status.value,
        "output": str(output_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply human adjudication decisions to produce a human-adjudicated dataset."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run(
            args.dataset,
            args.review,
            args.output,
            repo_root=args.repo_root,
            check=args.check,
        )
    except (HumanReviewApplicationError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
