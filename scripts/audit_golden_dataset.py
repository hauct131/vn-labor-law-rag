#!/usr/bin/env python3
"""Audit a golden dataset against a concrete JSONL chunk corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path(
            "data/evaluation/golden_questions_v4_canonical_word_candidate.json"
        ),
    )
    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path(
            "data/releases/"
            "labor-law-canonical-word-20260804-164432-candidate/"
            "canonical_chunks.jsonl"
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/evaluation/golden_v3_audit.json"),
    )
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    golden = load_json(args.golden)
    questions = golden.get("questions", golden)
    if not isinstance(questions, list):
        raise ValueError("Golden dataset must contain a questions list.")

    chunks = load_jsonl(args.chunks)
    chunk_by_id = {
        chunk["chunk_id"]: chunk
        for chunk in chunks
        if chunk.get("chunk_id")
    }
    article_codes = {
        chunk.get("article_code")
        for chunk in chunks
        if chunk.get("article_code")
    }

    ids = [q.get("id") for q in questions]
    duplicate_question_ids = sorted({
        qid for qid in ids if qid and ids.count(qid) > 1
    })

    missing_evidence_by_question: dict[str, list[str]] = {}
    missing_articles_by_question: dict[str, list[str]] = {}
    evidence_reference_count = 0
    evidence_found_count = 0

    for question in questions:
        qid = question.get("id", "<missing-id>")
        expected_ids = question.get("evidence_chunk_ids", [])
        missing_ids = []
        for chunk_id in expected_ids:
            evidence_reference_count += 1
            if chunk_id in chunk_by_id:
                evidence_found_count += 1
            else:
                missing_ids.append(chunk_id)
        if missing_ids:
            missing_evidence_by_question[qid] = missing_ids

        missing_codes = [
            code
            for code in question.get("expected_article_codes", [])
            if code not in article_codes
        ]
        if missing_codes:
            missing_articles_by_question[qid] = missing_codes

    actual_sha = sha256_file(args.chunks)
    declared_corpus = golden.get("corpus", {})
    declared_sha = declared_corpus.get("sha256")
    declared_count = declared_corpus.get("chunk_count")

    report = {
        "status": "PASS",
        "golden_path": str(args.golden),
        "chunks_path": str(args.chunks),
        "schema_version": golden.get("schema_version"),
        "locked": golden.get("locked"),
        "question_count": len(questions),
        "unique_question_ids": len(set(ids)),
        "duplicate_question_ids": duplicate_question_ids,
        "benchmark_enabled_count": sum(
            q.get("benchmark_enabled", True) for q in questions
        ),
        "answerable_count": sum(
            q.get("answerable") is True for q in questions
        ),
        "current_chunk_count": len(chunks),
        "current_chunk_sha256": actual_sha,
        "declared_chunk_count": declared_count,
        "declared_chunk_sha256": declared_sha,
        "declared_corpus_matches_current": (
            declared_count == len(chunks) and declared_sha == actual_sha
        ),
        "evidence_reference_count": evidence_reference_count,
        "evidence_found_count": evidence_found_count,
        "evidence_missing_count": evidence_reference_count - evidence_found_count,
        "affected_question_count": len(missing_evidence_by_question),
        "missing_evidence_by_question": missing_evidence_by_question,
        "missing_expected_articles_by_question": missing_articles_by_question,
    }

    failures = []
    if duplicate_question_ids:
        failures.append("duplicate_question_ids")
    if missing_evidence_by_question:
        failures.append("missing_evidence")
    if missing_articles_by_question:
        failures.append("missing_expected_articles")
    if failures:
        report["status"] = "FAIL"
        report["failures"] = failures

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.strict and failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
