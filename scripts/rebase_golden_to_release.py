#!/usr/bin/env python3
"""Rebind current-law golden evidence IDs to one immutable corpus release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(
            "data/evaluation/golden_questions_v2_current_law.json"
        ),
    )
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=Path(
            "data/releases/labor-law-2026-07-27-candidate"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/evaluation/golden_questions_v3_unified_candidate.json"
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(
            "data/evaluation/golden_v3_rebase_report.json"
        ),
    )
    args = parser.parse_args()

    source = load_json(args.input)
    manifest = load_json(args.release_dir / "manifest.json")
    chunks = load_jsonl(args.release_dir / "chunks.jsonl")

    chunks_by_code: dict[str, list[str]] = defaultdict(list)
    for chunk in chunks:
        code = chunk.get("article_code")
        chunk_id = chunk.get("chunk_id")
        if isinstance(code, str) and isinstance(chunk_id, str):
            chunks_by_code[code].append(chunk_id)

    missing_by_question: dict[str, list[str]] = {}
    rebound_questions: list[dict[str, Any]] = []
    changed_count = 0

    for source_question in source["questions"]:
        question = dict(source_question)
        expected_codes = question.get("expected_article_codes", [])
        missing = [code for code in expected_codes if not chunks_by_code.get(code)]
        if missing:
            missing_by_question[question["id"]] = missing
            continue

        evidence_ids: list[str] = []
        seen: set[str] = set()
        for code in expected_codes:
            for chunk_id in chunks_by_code[code]:
                if chunk_id not in seen:
                    seen.add(chunk_id)
                    evidence_ids.append(chunk_id)

        if evidence_ids != question.get("evidence_chunk_ids", []):
            changed_count += 1
        question["evidence_chunk_ids"] = evidence_ids
        question["corpus_verification"] = (
            "rebound_to_unified_candidate_release"
        )
        question["law_as_of"] = manifest["law_as_of"]
        question["evidence_mapping_policy"] = (
            "all_release_chunks_for_expected_article_codes"
        )
        rebound_questions.append(question)

    report = {
        "schema_version": "golden-rebase-report-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_golden": str(args.input),
        "release_id": manifest["release_id"],
        "release_status": manifest["release_status"],
        "questions_total": len(source["questions"]),
        "questions_rebound": len(rebound_questions),
        "questions_with_changed_evidence_ids": changed_count,
        "missing_by_question": missing_by_question,
        "all_expected_codes_resolved": not missing_by_question,
    }
    atomic_write(
        args.report,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    if missing_by_question:
        raise SystemExit(
            "Golden rebase failed; see missing_by_question in "
            f"{args.report}"
        )

    output = dict(source)
    output["schema_version"] = "golden-questions-v3-unified-candidate"
    output["dataset_status"] = (
        "rebased_to_unified_candidate_pending_authority_review"
    )
    output["locked"] = False
    output["created_at"] = "2026-07-27"
    output["law_as_of"] = manifest["law_as_of"]
    output["rebased_from"] = {
        "path": str(args.input),
        "sha256": sha256_file(args.input),
    }
    output["corpus"] = {
        "release_id": manifest["release_id"],
        "release_status": manifest["release_status"],
        "path": str(args.release_dir / "chunks.jsonl"),
        "article_path": str(args.release_dir / "articles.json"),
        "manifest_path": str(args.release_dir / "manifest.json"),
        "article_count": manifest["counts"]["article_containers"],
        "chunk_count": manifest["counts"]["chunks"],
        "sha256": sha256_file(args.release_dir / "chunks.jsonl"),
        "articles_sha256": sha256_file(args.release_dir / "articles.json"),
        "manifest_sha256": sha256_file(args.release_dir / "manifest.json"),
        "tokenizer": manifest["chunking"]["tokenizer"],
        "scope_note": (
            "Unified candidate: 16 VBPL documents plus official DOCX "
            "18/VBHN-VPQH and 66.18/2026/NQ-CP. Authority review remains "
            "pending, so this benchmark is not locked."
        ),
    }
    output["questions"] = rebound_questions
    output.setdefault("changelog", []).append(
        "Rebound every evidence_chunk_id to the immutable unified "
        "2026-07-27 candidate release; canonical article codes are now "
        "shared by corpus, chunks and golden labels."
    )
    output["labeling"] = dict(output.get("labeling", {}))
    output["labeling"]["question_count"] = len(rebound_questions)
    output["labeling"]["benchmark_enabled_count"] = sum(
        bool(question.get("benchmark_enabled", True))
        for question in rebound_questions
    )
    output["labeling"]["release_binding"] = {
        "release_id": manifest["release_id"],
        "manifest_sha256": sha256_file(
            args.release_dir / "manifest.json"
        ),
    }

    atomic_write(
        args.output,
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
