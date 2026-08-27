#!/usr/bin/env python3
"""Deterministically migrate the 20 held-out candidates to schema v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from evaluation.answer_quality.schema import AnswerQualityDataset


DEFAULT_SOURCE = Path("data/evaluation/heldout_20_review_candidate.json")
DEFAULT_CHUNKS = Path(
    "data/releases/labor-law-canonical-word-20260804-164432-candidate/"
    "canonical_chunks.jsonl"
)
DEFAULT_OUTPUT = Path(
    "data/evaluation/answer-quality-v1/candidate_questions.json"
)
CORPUS_RELEASE_ID = "labor-law-canonical-word-20260804-164432-candidate"


class CandidateMigrationError(ValueError):
    """Raised when source candidates cannot be migrated without guessing."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_repo_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise CandidateMigrationError(
            f"path must be inside repository: {resolved}"
        ) from exc


def _load_chunk_index(path: Path) -> dict[str, dict[str, Any]]:
    chunks: dict[str, dict[str, Any]] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise CandidateMigrationError(
                        f"invalid chunk JSON at line {line_number}"
                    ) from exc
                chunk_id = str(item.get("chunk_id", "")).strip()
                if not chunk_id or chunk_id in chunks:
                    raise CandidateMigrationError(
                        f"missing or duplicate chunk_id at line {line_number}"
                    )
                chunks[chunk_id] = item
    except OSError as exc:
        raise CandidateMigrationError(f"cannot read chunks: {path}") from exc
    return chunks


def build_dataset(
    source_path: Path,
    chunks_path: Path,
    *,
    repo_root: Path,
) -> AnswerQualityDataset:
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateMigrationError(
            f"cannot read source candidates: {source_path}"
        ) from exc

    if source.get("schema_version") != "heldout-evaluation-candidate-v1":
        raise CandidateMigrationError("unsupported source candidate schema")
    questions = source.get("questions")
    if not isinstance(questions, list) or source.get("question_count") != len(
        questions
    ):
        raise CandidateMigrationError("source question_count is inconsistent")

    chunk_index = _load_chunk_index(chunks_path)
    migrated: list[dict[str, Any]] = []
    for item in questions:
        case_id = str(item.get("id", "<missing>"))
        if item.get("answerable") is not True:
            raise CandidateMigrationError(
                f"{case_id}: false answerable label cannot be mapped safely"
            )
        if item.get("required_points"):
            raise CandidateMigrationError(
                f"{case_id}: non-empty required_points require human migration"
            )
        if item.get("benchmark_enabled") is not False:
            raise CandidateMigrationError(
                f"{case_id}: source candidate must not be benchmark-enabled"
            )

        evidence_ids = [str(value) for value in item["evidence_chunk_ids"]]
        missing = [value for value in evidence_ids if value not in chunk_index]
        if missing:
            raise CandidateMigrationError(
                f"{case_id}: evidence chunks are missing: {missing}"
            )
        expected_codes = set(item["expected_article_codes"])
        evidence_codes = {
            str(chunk_index[value].get("article_code", ""))
            for value in evidence_ids
        }
        if not expected_codes.issubset(evidence_codes):
            raise CandidateMigrationError(
                f"{case_id}: expected articles are not covered by evidence chunks"
            )

        migrated.append({
            "id": case_id,
            "question": item["question"],
            "category": item["category"],
            "question_type": item["question_type"],
            "difficulty": item["difficulty"],
            "document_number": item.get("document_number", ""),
            "document_title": item.get("document_title", ""),
            "retrieval_label_origin": item.get(
                "retrieval_label_origin",
                "",
            ),
            "expected_status": "answerable",
            "expected_article_codes": item["expected_article_codes"],
            "expected_article_ids": item["expected_article_ids"],
            "evidence_chunk_ids": evidence_ids,
            "evidence_excerpt": item["evidence_excerpt"],
            "required_claims": [],
            "forbidden_claims": [],
            "label_status": "pending_human_extraction",
            "authority_review_status": "pending",
            "reviewer": "",
            "review_notes": "",
            "benchmark_enabled": False,
        })

    try:
        return AnswerQualityDataset.model_validate({
            "schema_version": "answer-quality-dataset-v1",
            "dataset_status": "draft_candidate",
            "locked": False,
            "authority_review_status": "pending",
            "source_corpus_release_id": CORPUS_RELEASE_ID,
            "source_corpus_sha256": _sha256(chunks_path),
            "source_corpus_path": _relative_repo_path(chunks_path, repo_root),
            "source_dataset_path": _relative_repo_path(source_path, repo_root),
            "source_dataset_sha256": _sha256(source_path),
            "questions": migrated,
        })
    except ValueError as exc:
        raise CandidateMigrationError(
            "migrated candidate dataset violates answer-quality schema"
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
    source_path: Path,
    chunks_path: Path,
    output_path: Path,
    *,
    repo_root: Path,
    check: bool,
) -> dict[str, Any]:
    dataset = build_dataset(
        source_path,
        chunks_path,
        repo_root=repo_root,
    )
    rendered = _canonical_json(dataset)
    if check:
        if not output_path.is_file():
            raise CandidateMigrationError(f"output is missing: {output_path}")
        if output_path.read_text(encoding="utf-8") != rendered:
            raise CandidateMigrationError(f"output is stale: {output_path}")
        status = "check_pass"
    else:
        _atomic_write(output_path, rendered)
        status = "written"
    return {
        "status": status,
        "question_count": len(dataset.questions),
        "source_dataset_sha256": dataset.source_dataset_sha256,
        "source_corpus_sha256": dataset.source_corpus_sha256,
        "output": str(output_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run(
            args.source,
            args.chunks,
            args.output,
            repo_root=args.repo_root,
            check=args.check,
        )
    except CandidateMigrationError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
