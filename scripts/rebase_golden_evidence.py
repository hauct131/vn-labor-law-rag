#!/usr/bin/env python3
"""Rebase legacy evidence chunk IDs to a rebuilt Pháp điển corpus.

Legal labels and required points are preserved. Only missing evidence chunk IDs
are remapped. The legacy file is never overwritten.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


WORD_RE = re.compile(r"[0-9a-zA-ZÀ-ỹĐđ]+", re.UNICODE)


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


def normalize(value: Any) -> str:
    text = str(value or "").lower()
    text = unicodedata.normalize("NFKC", text)
    return " ".join(WORD_RE.findall(text))


def token_set(value: Any) -> set[str]:
    return set(normalize(value).split())


def token_f1(a: Any, b: Any) -> float:
    left = token_set(a)
    right = token_set(b)
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    precision = overlap / len(right)
    recall = overlap / len(left)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def ratio(a: Any, b: Any) -> float:
    left = normalize(a)
    right = normalize(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def candidate_score(
    old: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[float, dict[str, float | bool]]:
    same_key = bool(
        old.get("chunk_key")
        and old.get("chunk_key") == candidate.get("chunk_key")
    )
    same_type = bool(
        old.get("chunk_type")
        and old.get("chunk_type") == candidate.get("chunk_type")
    )

    body_ratio = ratio(old.get("body_text"), candidate.get("body_text"))
    content_ratio = ratio(old.get("content"), candidate.get("content"))
    heading_ratio = ratio(old.get("heading"), candidate.get("heading"))
    body_f1 = token_f1(old.get("body_text"), candidate.get("body_text"))

    score = (
        0.38 * body_ratio
        + 0.24 * content_ratio
        + 0.20 * body_f1
        + 0.08 * heading_ratio
        + 0.05 * float(same_type)
        + 0.05 * float(same_key)
    )
    if same_key:
        score = max(score, 0.98)

    return score, {
        "same_chunk_key": same_key,
        "same_chunk_type": same_type,
        "body_ratio": round(body_ratio, 6),
        "content_ratio": round(content_ratio, 6),
        "body_token_f1": round(body_f1, 6),
        "heading_ratio": round(heading_ratio, 6),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path("data/evaluation/golden_questions_v1_legacy.json"),
    )
    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path("data/processed/legal_chunks.jsonl"),
    )
    parser.add_argument(
        "--legacy-catalog",
        type=Path,
        default=Path("data/evaluation/legacy_evidence_catalog.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/evaluation/"
            "golden_questions_v1_rebased_current_phapdien.json"
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/evaluation/golden_rebase_report.json"),
    )
    parser.add_argument("--min-score", type=float, default=0.60)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    original = load_json(args.golden)
    catalog = load_json(args.legacy_catalog).get("chunks", {})
    chunks = load_jsonl(args.chunks)

    current_by_id = {
        chunk["chunk_id"]: chunk
        for chunk in chunks
        if chunk.get("chunk_id")
    }
    by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        article_code = chunk.get("article_code")
        if article_code:
            by_article[article_code].append(chunk)

    rebased = copy.deepcopy(original)
    replacements = []
    unresolved = []
    low_confidence = []

    for question in rebased["questions"]:
        qid = question["id"]
        old_evidence_ids = list(question.get("evidence_chunk_ids", []))
        new_evidence_ids = []
        used_ids = set()

        for old_id in old_evidence_ids:
            if old_id in current_by_id:
                new_evidence_ids.append(old_id)
                used_ids.add(old_id)
                continue

            old_chunk = catalog.get(old_id)
            if not old_chunk:
                unresolved.append({
                    "question_id": qid,
                    "old_chunk_id": old_id,
                    "reason": "missing_from_legacy_catalog",
                })
                continue

            article_code = old_chunk.get("article_code")
            candidate_pool = [
                candidate
                for candidate in by_article.get(article_code, [])
                if candidate.get("chunk_id") not in used_ids
            ]

            if not candidate_pool:
                unresolved.append({
                    "question_id": qid,
                    "old_chunk_id": old_id,
                    "article_code": article_code,
                    "reason": "no_current_candidate_for_article",
                })
                continue

            scored = []
            for candidate in candidate_pool:
                score, components = candidate_score(old_chunk, candidate)
                scored.append((score, candidate, components))
            scored.sort(key=lambda item: (-item[0], item[1].get("chunk_id", "")))

            score, best, components = scored[0]
            detail = {
                "question_id": qid,
                "old_chunk_id": old_id,
                "new_chunk_id": best.get("chunk_id"),
                "article_code": article_code,
                "old_chunk_key": old_chunk.get("chunk_key"),
                "new_chunk_key": best.get("chunk_key"),
                "score": round(score, 6),
                "score_components": components,
                "runner_up_score": (
                    round(scored[1][0], 6) if len(scored) > 1 else None
                ),
            }

            if score < args.min_score:
                detail["reason"] = "below_min_score"
                unresolved.append(detail)
                continue

            new_id = best["chunk_id"]
            new_evidence_ids.append(new_id)
            used_ids.add(new_id)
            replacements.append(detail)
            if score < 0.80:
                low_confidence.append(detail)

        question["evidence_chunk_ids"] = new_evidence_ids
        changed = [
            item
            for item in replacements
            if item["question_id"] == qid
        ]
        if changed:
            question["evidence_rebase"] = changed

    actual_sha = sha256_file(args.chunks)
    original_corpus = copy.deepcopy(original.get("corpus", {}))
    rebased["schema_version"] = "golden-questions-v1-rebased-phapdien"
    rebased["dataset_status"] = (
        "legacy_legal_labels_with_evidence_rebased_to_current_phapdien"
    )
    rebased["locked"] = False
    rebased["corpus"] = {
        "path": str(args.chunks),
        "chunk_count": len(chunks),
        "sha256": actual_sha,
        "rebase_source_corpus": original_corpus,
        "scope_note": (
            "Only evidence chunk IDs were rebased. Legal labels remain legacy "
            "and must not be treated as the final current-law benchmark."
        ),
    }
    rebased["evidence_rebase_summary"] = {
        "replaced_count": len(replacements),
        "unresolved_count": len(unresolved),
        "low_confidence_count": len(low_confidence),
        "minimum_score": args.min_score,
    }

    report = {
        "status": "PASS" if not unresolved else "FAIL",
        "golden_input": str(args.golden),
        "current_chunks": str(args.chunks),
        "output": str(args.output),
        "current_chunk_count": len(chunks),
        "current_chunk_sha256": actual_sha,
        "replacements": replacements,
        "unresolved": unresolved,
        "low_confidence": low_confidence,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(rebased, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and unresolved:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
