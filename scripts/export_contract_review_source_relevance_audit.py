#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from app.services.contract_review_service import (
    CATEGORIES,
    _excerpt_for,
    _paragraphs,
    evidence_retriever,
)


def _obj_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        try:
            value = obj.model_dump()
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            value = obj.dict()
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    value = getattr(obj, "__dict__", None)
    return dict(value) if isinstance(value, dict) else {}


def _first(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _payload_for_source(retriever: Any, data: dict[str, Any], article_code: str) -> dict[str, Any]:
    chunks = getattr(retriever, "chunks", []) or []
    chunk_id = _first(data, "chunk_id", "source_id")
    if chunk_id:
        for payload in chunks:
            if str(payload.get("chunk_id") or "") == chunk_id:
                return dict(payload)
    if article_code:
        for payload in chunks:
            code = str(payload.get("article_code") or payload.get("codification_code") or "")
            if code == article_code:
                return dict(payload)
    return {}


def _load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases")
    if not isinstance(cases, list):
        raise SystemExit("ERROR: cases JSON must contain a top-level 'cases' list")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export 0/1/2 manual relevance audit rows for Contract Review runtime sources."
    )
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=4)
    args = parser.parse_args()

    cases = _load_cases(args.cases)
    rules = {rule.key: rule for rule in CATEGORIES}
    retriever = evidence_retriever()

    fieldnames = [
        "audit_id",
        "case_id",
        "category",
        "case_text",
        "detected_excerpt",
        "expected_article_codes",
        "source_rank",
        "article_code",
        "article_title",
        "is_in_minimum_expected_set",
        "is_in_current_preferred_set",
        "source_score",
        "source_text",
        "manual_label",
        "manual_reason",
    ]

    rows: list[dict[str, Any]] = []

    for case in cases:
        case_id = str(case.get("id") or case.get("case_id") or "").strip()
        category = str(case.get("category") or "").strip()
        text = str(case.get("text") or "").strip()
        expected = [str(x) for x in (case.get("expected_article_codes") or [])]
        expected_set = set(expected)

        if category not in rules:
            raise SystemExit(f"ERROR: unknown category for {case_id}: {category}")

        rule = rules[category]
        excerpt = _excerpt_for(rule, _paragraphs(text))
        query = rule.query + (f" Nội dung hợp đồng: {excerpt[:500]}" if excerpt else "")

        sources = retriever.retrieve(
            query,
            top_k=args.top_k,
            preferred_article_codes=rule.preferred_article_codes,
        )

        for rank, source in enumerate(sources, start=1):
            data = _obj_dict(source)
            article_code = _first(data, "article_code", "codification_code") or str(
                getattr(source, "article_code", "") or ""
            )
            payload = _payload_for_source(retriever, data, article_code)

            article_title = (
                _first(data, "article_title", "title", "source_title")
                or _first(payload, "article_title", "title")
            )
            source_text = (
                _first(data, "content_preview", "content", "text", "excerpt", "quote")
                or _first(payload, "content_preview", "content", "text")
            )
            source_score = data.get("score", data.get("raw_score", ""))

            rows.append(
                {
                    "audit_id": f"{case_id}__r{rank}",
                    "case_id": case_id,
                    "category": category,
                    "case_text": text,
                    "detected_excerpt": excerpt,
                    "expected_article_codes": ";".join(expected),
                    "source_rank": rank,
                    "article_code": article_code,
                    "article_title": article_title,
                    "is_in_minimum_expected_set": 1 if article_code in expected_set else 0,
                    "is_in_current_preferred_set": 1 if article_code in rule.preferred_article_codes else 0,
                    "source_score": source_score,
                    "source_text": source_text,
                    "manual_label": "",
                    "manual_reason": "",
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    counts_by_category: dict[str, int] = {}
    for row in rows:
        counts_by_category[row["category"]] = counts_by_category.get(row["category"], 0) + 1

    print(
        json.dumps(
            {
                "output": str(args.output),
                "case_count": len(cases),
                "audit_row_count": len(rows),
                "top_k": args.top_k,
                "rows_by_category": counts_by_category,
                "instruction": "Fill manual_label with 2, 1, or 0 only; optionally explain manual_reason.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
