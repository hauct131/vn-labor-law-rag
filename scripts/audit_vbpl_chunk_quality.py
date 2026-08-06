#!/usr/bin/env python3
"""
Audit VBPL legal chunks quality and generate data/quality/vbpl_chunk_quality_report.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit VBPL chunk quality and provenance.")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path("data/processed/vbpl_articles_raw.json"),
    )
    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path("data/processed/vbpl_legal_chunks.jsonl"),
    )
    parser.add_argument(
        "--e5-summary",
        type=Path,
        default=Path("data/quality/vbpl_e5_token_audit/summary.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/quality/vbpl_chunk_quality_report.json"),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.corpus.exists():
        print(f"ERROR: Corpus file not found: {args.corpus}")
        return 1
    if not args.chunks.exists():
        print(f"ERROR: Chunks file not found: {args.chunks}")
        return 1

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    raw_articles = corpus.get("articles", [])
    expected_article_ids = {a["article_id"] for a in raw_articles if isinstance(a, dict) and "article_id" in a}
    expected_doc_numbers = {a["document_number"] for a in raw_articles if isinstance(a, dict) and "document_number" in a}

    chunk_lines = args.chunks.read_text(encoding="utf-8").strip().split("\n")
    chunks = [json.loads(line) for line in chunk_lines if line.strip()]

    chunk_ids = [c["chunk_id"] for c in chunks if isinstance(c, dict) and "chunk_id" in c]
    dup_ids = len(chunk_ids) - len(set(chunk_ids))

    covered_article_ids = {
        c.get("parent_article_id") or c.get("article_id")
        for c in chunks
        if isinstance(c, dict)
    }
    covered_article_ids.discard(None)
    missing_article_coverage = len(expected_article_ids - covered_article_ids)

    covered_doc_numbers = {c.get("document_number") for c in chunks if isinstance(c, dict)}
    covered_doc_numbers.discard(None)

    empty_chunks = sum(1 for c in chunks if not str(c.get("content", "")).strip())

    required_fields = [
        "chunk_id",
        "article_id",
        "document_id",
        "document_number",
        "source_document_id",
        "source_item_id",
        "source_adapter",
        "corpus_role",
        "source_urls",
        "source_sha256",
    ]

    missing_provenance_metadata = 0
    for c in chunks:
        if not isinstance(c, dict):
            missing_provenance_metadata += 1
            continue
        for req in required_fields:
            val = c.get(req)
            if val is None or (isinstance(val, (str, list)) and len(val) == 0):
                missing_provenance_metadata += 1
                break

    hard_token_overflow = 0
    if args.e5_summary.exists():
        e5_data = json.loads(args.e5_summary.read_text(encoding="utf-8"))
        hard_token_overflow = e5_data.get("strictly_over_model_limit", 0)

    documents_processed = len(covered_doc_numbers)
    selected_articles = len(expected_article_ids)
    article_coverage = len(covered_article_ids)
    appendix_leakage_articles = corpus.get("metadata", {}).get("appendix_leakage_articles", [])
    administrative_tail_leakage_articles = corpus.get("metadata", {}).get("administrative_tail_leakage_articles", [])

    status = (
        "PASS"
        if (
            documents_processed == 16
            and selected_articles == 285
            and article_coverage == 285
            and dup_ids == 0
            and empty_chunks == 0
            and missing_provenance_metadata == 0
            and hard_token_overflow == 0
            and len(appendix_leakage_articles) == 0
            and len(administrative_tail_leakage_articles) == 0
        )
        else "FAIL"
    )

    report = {
        "documents": documents_processed,
        "selected_articles": selected_articles,
        "article_coverage": article_coverage,
        "missing_article_coverage": missing_article_coverage,
        "duplicate_chunk_ids": dup_ids,
        "empty_chunks": empty_chunks,
        "missing_provenance_metadata": missing_provenance_metadata,
        "hard_token_overflow": hard_token_overflow,
        "appendix_leakage_articles": appendix_leakage_articles,
        "administrative_tail_leakage_articles": administrative_tail_leakage_articles,
        "total_chunks": len(chunks),
        "status": status,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.strict and status != "PASS":
        sys.exit(1)

    return 0


if __name__ == "__main__":
    sys.exit(main())
