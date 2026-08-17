#!/usr/bin/env python3
"""Apply reviewed multi-model candidates without enabling a benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evaluation.answer_quality.adjudication import ManualAdjudicationArtifact
from evaluation.answer_quality.schema import (
    AnswerQualityDataset,
    AuthorityReviewStatus,
    DatasetStatus,
    LabelStatus,
    load_answer_quality_dataset,
)


DEFAULT_DATASET = Path(
    "data/evaluation/answer-quality-v1/candidate_questions.json"
)
DEFAULT_ADJUDICATION = Path(
    "data/evaluation/answer-quality-v1/adjudications/"
    "multi_llm_adjudicated_20.json"
)
DEFAULT_OUTPUT = Path(
    "data/evaluation/answer-quality-v1/multi_llm_reviewed_questions.json"
)


class AdjudicationApplicationError(ValueError):
    """Raised when an adjudication cannot be applied without guessing."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_artifact(path: Path) -> ManualAdjudicationArtifact:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ManualAdjudicationArtifact.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise AdjudicationApplicationError(
            f"invalid adjudication artifact: {path}"
        ) from exc


def _load_chunk_index(path: Path) -> dict[str, dict[str, Any]]:
    chunks: dict[str, dict[str, Any]] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AdjudicationApplicationError(
                        f"invalid corpus JSON at line {line_number}"
                    ) from exc
                chunk_id = str(item.get("chunk_id", "")).strip()
                if not chunk_id or chunk_id in chunks:
                    raise AdjudicationApplicationError(
                        f"missing or duplicate chunk ID at line {line_number}"
                    )
                chunks[chunk_id] = item
    except OSError as exc:
        raise AdjudicationApplicationError(
            f"cannot read canonical corpus: {path}"
        ) from exc
    return chunks


def build_reviewed_dataset(
    dataset_path: Path,
    adjudication_path: Path,
    *,
    repo_root: Path,
) -> AnswerQualityDataset:
    source = load_answer_quality_dataset(dataset_path)
    artifact = _load_artifact(adjudication_path)

    if source.dataset_status != DatasetStatus.DRAFT_CANDIDATE or source.locked:
        raise AdjudicationApplicationError(
            "adjudication input must be an unlocked draft candidate"
        )
    if source.authority_review_status != AuthorityReviewStatus.PENDING:
        raise AdjudicationApplicationError("source authority status must be pending")
    if any(question.benchmark_enabled for question in source.questions):
        raise AdjudicationApplicationError(
            "source questions must not be benchmark-enabled"
        )

    corpus_path = repo_root / source.source_corpus_path
    if not corpus_path.is_file():
        raise AdjudicationApplicationError(
            f"declared canonical corpus is missing: {corpus_path}"
        )
    if _sha256(corpus_path) != source.source_corpus_sha256:
        raise AdjudicationApplicationError("canonical corpus hash mismatch")
    chunk_index = _load_chunk_index(corpus_path)

    source_ids = [question.id for question in source.questions]
    artifact_by_id = {case.case_id: case for case in artifact.cases}
    missing = [case_id for case_id in source_ids if case_id not in artifact_by_id]
    extra = [case_id for case_id in artifact_by_id if case_id not in source_ids]
    if missing or extra:
        raise AdjudicationApplicationError(
            f"adjudication coverage mismatch: missing={missing}, extra={extra}"
        )

    reviewed_questions: list[dict[str, Any]] = []
    for question in source.questions:
        case = artifact_by_id[question.id]
        if case.expected_status != question.expected_status:
            raise AdjudicationApplicationError(
                f"{question.id}: adjudication cannot silently change answer status"
            )
        allowed_articles = set(question.expected_article_codes)
        allowed_chunks = set(question.evidence_chunk_ids)
        for claim in case.required_claims:
            if not set(claim.supported_by_article_codes).issubset(
                allowed_articles
            ):
                raise AdjudicationApplicationError(
                    f"{question.id}/{claim.claim_id}: article outside allowlist"
                )
            if not set(claim.supported_by_chunk_ids).issubset(allowed_chunks):
                raise AdjudicationApplicationError(
                    f"{question.id}/{claim.claim_id}: chunk outside allowlist"
                )
            for chunk_id in claim.supported_by_chunk_ids:
                chunk = chunk_index.get(chunk_id)
                if chunk is None:
                    raise AdjudicationApplicationError(
                        f"{question.id}/{claim.claim_id}: corpus chunk is missing"
                    )
                if chunk.get("article_code") not in (
                    claim.supported_by_article_codes
                ):
                    raise AdjudicationApplicationError(
                        f"{question.id}/{claim.claim_id}: chunk/article mismatch"
                    )

        payload = question.model_dump(mode="json")
        payload.update({
            "expected_status": case.expected_status.value,
            "required_claims": [
                claim.model_dump(mode="json")
                for claim in case.required_claims
            ],
            "forbidden_claims": [
                claim.model_dump(mode="json")
                for claim in case.forbidden_claims
            ],
            "label_status": LabelStatus.MULTI_LLM_CANDIDATE.value,
            "authority_review_status": AuthorityReviewStatus.PENDING.value,
            "reviewer": "",
            "review_notes": (
                f"Multi-LLM decision={case.decision}. "
                f"{case.adjudication_notes} Human review is required."
            ),
            "benchmark_enabled": False,
        })
        reviewed_questions.append(payload)

    try:
        return AnswerQualityDataset.model_validate({
            **source.model_dump(mode="json"),
            "dataset_status": DatasetStatus.MULTI_LLM_REVIEWED.value,
            "locked": False,
            "authority_review_status": AuthorityReviewStatus.PENDING.value,
            "questions": reviewed_questions,
        })
    except ValidationError as exc:
        raise AdjudicationApplicationError(
            "applied adjudication violates the answer-quality dataset schema"
        ) from exc


def _canonical_json(dataset: AnswerQualityDataset) -> str:
    return json.dumps(
        dataset.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def run(
    dataset_path: Path,
    adjudication_path: Path,
    output_path: Path,
    *,
    repo_root: Path,
    check: bool,
) -> dict[str, Any]:
    dataset = build_reviewed_dataset(
        dataset_path,
        adjudication_path,
        repo_root=repo_root,
    )
    rendered = _canonical_json(dataset)
    if check:
        if not output_path.is_file():
            raise AdjudicationApplicationError(f"output is missing: {output_path}")
        if output_path.read_text(encoding="utf-8") != rendered:
            raise AdjudicationApplicationError(f"output is stale: {output_path}")
        status = "check_pass"
    else:
        _atomic_write(output_path, rendered)
        status = "written"
    return {
        "status": status,
        "question_count": len(dataset.questions),
        "required_claim_count": sum(
            len(question.required_claims) for question in dataset.questions
        ),
        "forbidden_claim_count": sum(
            len(question.forbidden_claims) for question in dataset.questions
        ),
        "benchmark_enabled_count": sum(
            question.benchmark_enabled for question in dataset.questions
        ),
        "adjudication_sha256": _sha256(adjudication_path),
        "source_corpus_sha256": dataset.source_corpus_sha256,
        "output": str(output_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--adjudication",
        type=Path,
        default=DEFAULT_ADJUDICATION,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run(
            args.dataset,
            args.adjudication,
            args.output,
            repo_root=args.repo_root,
            check=args.check,
        )
    except (AdjudicationApplicationError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
