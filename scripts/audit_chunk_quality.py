#!/usr/bin/env python3
"""
Prepare an expanded, deterministic manual audit pack for legal chunks.

Outputs:
- chunk_quality_audit_summary.json
- chunk_quality_review_items.json
- chunk_quality_review.csv
- chunk_quality_review.md

Standard-library only. The script does not modify parser/chunker output.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

DEFAULT_CONTROL_TARGETS = {
    "article": 8,
    "clause": 12,
    "points": 8,
    "table": 8,
    "preamble": 4,
}

APPENDIX_RE = re.compile(
    r"(?im)^\s*(PHỤ\s+LỤC(?:\s+[IVXLC0-9]+)?|"
    r"HỆ\s+THỐNG\s+BIỂU\s+MẪU|Mẫu\s+số\s+[0-9IVXLC]+[a-z]?)\b"
)

FORMULA_CUE_RE = re.compile(
    r"(?i)(được\s+tính\s+như\s+sau|"
    r"được\s+xác\s+định\s+như\s+sau|"
    r"theo\s+công\s+thức|"
    r"sẽ\s+là\s*:|"
    r"kết\s+quả\s+là\s*:)"
)

SUBPOINT_START_RE = re.compile(r"^\s*[a-zđ]\d+\)", re.IGNORECASE)
CLAUSE_START_RE = re.compile(r"^\s*\d+\.")
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare expanded manual-review files for legal chunks."
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path("data/processed/articles_raw.json"),
    )
    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path("data/processed/legal_chunks.jsonl"),
    )
    parser.add_argument(
        "--previous-review",
        type=Path,
        default=Path("data/processed/manual_review_30.md"),
        help="Optional previous review file; its chunk IDs are excluded from controls.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/chunk_quality_audit"),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260718,
    )
    parser.add_argument(
        "--suspicious-limit",
        type=int,
        default=20,
        help="Maximum non-fallback suspicious chunks to include.",
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for line_no, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError(f"JSONL line {line_no} is not an object")
        chunks.append(obj)
    return chunks


def extract_text(chunk: dict[str, Any]) -> str:
    for key in ("content", "body", "page_content", "text"):
        value = chunk.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def extract_body(chunk: dict[str, Any]) -> str:
    text = extract_text(chunk)
    if "\n\n" in text:
        return text.split("\n\n", 1)[1].strip()
    return text.strip()


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def point_marker_present(body: str, labels: Iterable[str]) -> bool:
    for label in labels:
        pattern = re.compile(
            rf"(?im)^\s*{re.escape(str(label))}\)"
        )
        if pattern.search(body):
            return True
    return False


def parse_previous_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(UUID_RE.findall(path.read_text(encoding="utf-8")))


def build_canonical_maps(
    corpus: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, dict[str, Any]]],
    dict[str, dict[str, Any]],
]:
    articles: dict[str, dict[str, Any]] = {}
    units_by_article: dict[str, dict[str, dict[str, Any]]] = {}
    tables: dict[str, dict[str, Any]] = {}

    for article in corpus.get("articles", []):
        if not isinstance(article, dict):
            continue
        article_id = article.get("article_id")
        if not isinstance(article_id, str):
            continue

        articles[article_id] = article
        units_by_article[article_id] = {
            unit.get("unit_id"): unit
            for unit in article.get("content_units", [])
            if isinstance(unit, dict)
            and isinstance(unit.get("unit_id"), str)
        }

        for table in article.get("tables", []):
            if (
                isinstance(table, dict)
                and isinstance(table.get("table_id"), str)
            ):
                tables[table["table_id"]] = table

    for attachment in corpus.get("attachments", []):
        if not isinstance(attachment, dict):
            continue
        for table in attachment.get("tables", []):
            if (
                isinstance(table, dict)
                and isinstance(table.get("table_id"), str)
            ):
                tables[table["table_id"]] = table

    return articles, units_by_article, tables


def detect_flags(
    chunk: dict[str, Any],
    tables: dict[str, dict[str, Any]],
) -> list[str]:
    flags: list[str] = []
    body = extract_body(chunk)
    chunk_type = chunk.get("chunk_type")
    clause_number = chunk.get("clause_number")
    point_labels = chunk.get("point_labels") or []
    source_unit_ids = chunk.get("source_unit_ids") or []
    segment_index = chunk.get("segment_index")
    token_count = chunk.get("token_count") or 0

    if point_labels and not point_marker_present(body, point_labels):
        flags.append("metadata_point_missing_in_body")

    if (
        chunk_type == "fallback_segment"
        and isinstance(segment_index, int)
        and segment_index > 1
        and SUBPOINT_START_RE.search(body)
    ):
        flags.append("subpoint_without_parent_context")

    if APPENDIX_RE.search(body):
        flags.append("appendix_or_form_marker")

        if clause_number not in (None, ""):
            flags.append("attachment_attached_to_clause")
        if (
            chunk_type == "table"
            and chunk.get("container_type") != "attachment"
        ):
            flags.append("attachment_wrong_container")

    if (
        FORMULA_CUE_RE.search(body)
        and not chunk.get("formula_ids")
        and not chunk.get("related_table_ids")
    ):
        flags.append("formula_cue_requires_source_check")

    if len(source_unit_ids) >= 20:
        flags.append("many_source_units")

    if isinstance(token_count, int) and token_count >= 700:
        flags.append("near_token_limit")

    if chunk_type == "fallback_segment":
        if not body:
            flags.append("empty_fallback_body")
        elif (
            isinstance(segment_index, int)
            and segment_index > 1
            and not CLAUSE_START_RE.search(body)
            and not point_marker_present(body, point_labels)
            and not SUBPOINT_START_RE.search(body)
            and not APPENDIX_RE.search(body)
            and "[Ngữ cảnh khoản]" not in body
        ):
            flags.append("continuation_without_visible_parent_marker")

    if chunk_type == "table":
        table_id = chunk.get("table_id")
        table = tables.get(table_id) if isinstance(table_id, str) else None
        if table:
            rows = table.get("rows") or []
            row_lengths = {
                len(row)
                for row in rows
                if isinstance(row, list)
            }
            if len(row_lengths) > 1:
                flags.append("table_multilevel_or_irregular_rows")
            if not table.get("headers") and len(rows) > 1:
                flags.append("table_headers_not_explicit")

    return flags


FLAG_WEIGHTS = {
    "attachment_wrong_container": 110,
    "attachment_attached_to_clause": 100,
    "metadata_point_missing_in_body": 90,
    "subpoint_without_parent_context": 80,
    "formula_cue_requires_source_check": 70,
    "appendix_or_form_marker": 60,
    "continuation_without_visible_parent_marker": 50,
    "many_source_units": 30,
    "near_token_limit": 20,
    "table_multilevel_or_irregular_rows": 15,
    "table_headers_not_explicit": 10,
}


def severity(flags: list[str]) -> int:
    return sum(FLAG_WEIGHTS.get(flag, 1) for flag in flags)


def sample_controls(
    chunks: list[dict[str, Any]],
    excluded_ids: set[str],
    seed: int,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []

    for chunk_type, target in DEFAULT_CONTROL_TARGETS.items():
        pool = [
            chunk
            for chunk in chunks
            if chunk.get("chunk_type") == chunk_type
            and chunk.get("chunk_id") not in excluded_ids
        ]
        pool.sort(
            key=lambda chunk: (
                str(chunk.get("source_type") or ""),
                int(chunk.get("token_count") or 0),
                str(chunk.get("chunk_key") or ""),
            )
        )

        if len(pool) <= target:
            chosen = pool
        else:
            chosen = rng.sample(pool, target)
            chosen.sort(key=lambda chunk: str(chunk.get("chunk_key") or ""))

        selected.extend(chosen)

    return selected


def source_units_for_chunk(
    chunk: dict[str, Any],
    units_by_article: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    article_id = (
        chunk.get("parent_article_id")
        or chunk.get("article_id")
    )
    unit_map = units_by_article.get(str(article_id), {})
    result: list[dict[str, Any]] = []

    for unit_id in chunk.get("source_unit_ids") or []:
        unit = unit_map.get(unit_id)
        if unit is not None:
            result.append(unit)

    return result


def write_csv(
    path: Path,
    items: list[dict[str, Any]],
) -> None:
    fieldnames = [
        "review_no",
        "category",
        "chunk_id",
        "chunk_key",
        "chunk_type",
        "article_code",
        "parent_article_id",
        "clause_number",
        "point_labels",
        "segment_index",
        "token_count",
        "risk_flags",
        "decision",
        "error_category",
        "notes",
    ]

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        for item in items:
            chunk = item["chunk"]
            writer.writerow(
                {
                    "review_no": item["review_no"],
                    "category": item["category"],
                    "chunk_id": chunk.get("chunk_id"),
                    "chunk_key": chunk.get("chunk_key"),
                    "chunk_type": chunk.get("chunk_type"),
                    "article_code": chunk.get("article_code"),
                    "parent_article_id": (
                        chunk.get("parent_article_id")
                        or chunk.get("article_id")
                    ),
                    "clause_number": chunk.get("clause_number"),
                    "point_labels": ",".join(
                        str(value)
                        for value in (chunk.get("point_labels") or [])
                    ),
                    "segment_index": chunk.get("segment_index"),
                    "token_count": chunk.get("token_count"),
                    "risk_flags": ",".join(item["risk_flags"]),
                    "decision": "",
                    "error_category": "",
                    "notes": "",
                }
            )


def preview(text: str, limit: int = 1200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[TRUNCATED]..."


def write_markdown(
    path: Path,
    items: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    lines: list[str] = [
        "# EXPANDED CHUNK QUALITY AUDIT",
        "",
        "## Summary",
        "",
        f"- Total corpus chunks: {summary['corpus_chunk_count']}",
        f"- Selected for this audit: {summary['selected_count']}",
        f"- All fallback segments selected: {summary['fallback_selected']}",
        (
            "- Attachment table chunks selected: "
            f"{summary['attachment_table_selected']}/"
            f"{summary['attachment_table_chunk_count']}"
        ),
        (
            "- Formula-linked chunks selected: "
            f"{summary['formula_linked_selected']}/"
            f"{summary['formula_linked_chunk_count']}"
        ),
        (
            "- Multi-segment table chunks selected: "
            f"{summary['multisegment_table_selected']}/"
            f"{summary['multisegment_table_chunk_count']}"
        ),
        f"- Suspicious non-fallback selected: {summary['suspicious_selected']}",
        f"- Stratified controls selected: {summary['control_selected']}",
        f"- Previous review IDs detected: {summary['previous_review_id_count']}",
        "",
        "## Manual decision values",
        "",
        "- `PASS`: nội dung và metadata phù hợp.",
        "- `FAIL_PARSER`: lỗi có từ canonical corpus/parser.",
        "- `FAIL_CHUNKER`: lỗi tách chunk, metadata hoặc context.",
        "- `LIMITATION`: chấp nhận tạm thời và ghi rõ giới hạn.",
        "- `NEEDS_SOURCE_CHECK`: cần đối chiếu HTML gốc.",
        "",
    ]

    for item in items:
        chunk = item["chunk"]
        source_units = item["source_units"]
        body = extract_body(chunk)

        metadata = {
            "chunk_id": chunk.get("chunk_id"),
            "chunk_key": chunk.get("chunk_key"),
            "chunk_type": chunk.get("chunk_type"),
            "unit_type": chunk.get("unit_type"),
            "parent_article_id": (
                chunk.get("parent_article_id")
                or chunk.get("article_id")
            ),
            "article_code": chunk.get("article_code"),
            "article_title": chunk.get("article_title"),
            "chapter_title": chunk.get("chapter_title"),
            "clause_number": chunk.get("clause_number"),
            "point_labels": chunk.get("point_labels"),
            "segment_index": chunk.get("segment_index"),
            "token_count": chunk.get("token_count"),
            "source_type": chunk.get("source_type"),
            "source_unit_count": len(chunk.get("source_unit_ids") or []),
        }

        source_preview = [
            {
                "unit_id": unit.get("unit_id"),
                "unit_type": unit.get("unit_type"),
                "clause_number": unit.get("clause_number"),
                "point_label": unit.get("point_label"),
                "text": preview(str(unit.get("text") or ""), 700),
            }
            for unit in source_units
        ]

        lines.extend(
            [
                "---",
                "",
                (
                    f"## Review {item['review_no']:03d} — "
                    f"`{item['category']}` — "
                    f"`{chunk.get('chunk_type')}`"
                ),
                "",
                f"**Automated flags:** `{', '.join(item['risk_flags']) or 'none'}`",
                "",
                "### Metadata",
                "",
                "```json",
                json.dumps(metadata, ensure_ascii=False, indent=2),
                "```",
                "",
                "### Chunk body",
                "",
                "```text",
                preview(body, 2500),
                "```",
                "",
                "### Canonical source-unit preview",
                "",
                "```json",
                json.dumps(source_preview, ensure_ascii=False, indent=2),
                "```",
                "",
                "### Manual result",
                "",
                "- [ ] Đúng article/chapter.",
                "- [ ] Đúng clause/point metadata.",
                "- [ ] Không lẫn cấu trúc khác.",
                "- [ ] Đủ parent context khi truy hồi độc lập.",
                "- [ ] Khớp canonical source units.",
                "- [ ] Không có dấu hiệu mất công thức/hình/bảng.",
                "- [ ] `PASS`",
                "- [ ] `FAIL_PARSER`",
                "- [ ] `FAIL_CHUNKER`",
                "- [ ] `LIMITATION`",
                "- [ ] `NEEDS_SOURCE_CHECK`",
                "",
                "**Error category:**",
                "",
                "**Notes:**",
                "",
            ]
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()

    corpus = load_json(args.corpus)
    if not isinstance(corpus, dict):
        raise ValueError("Canonical corpus must be a JSON object")

    chunks = load_jsonl(args.chunks)
    articles, units_by_article, tables = build_canonical_maps(corpus)
    previous_ids = parse_previous_ids(args.previous_review)

    flags_by_id: dict[str, list[str]] = {}
    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id") or "")
        flags_by_id[chunk_id] = detect_flags(chunk, tables)

    fallback = [
        chunk
        for chunk in chunks
        if chunk.get("chunk_type") == "fallback_segment"
    ]
    fallback.sort(key=lambda chunk: str(chunk.get("chunk_key") or ""))

    fallback_ids = {
        str(chunk.get("chunk_id") or "")
        for chunk in fallback
    }

    table_segment_counts = Counter(
        chunk.get("table_id")
        for chunk in chunks
        if chunk.get("chunk_type") == "table" and chunk.get("table_id")
    )
    attachment_tables = [
        chunk
        for chunk in chunks
        if chunk.get("chunk_type") == "table"
        and chunk.get("container_type") == "attachment"
    ]
    formula_linked = [
        chunk
        for chunk in chunks
        if chunk.get("formula_ids") or chunk.get("related_table_ids")
    ]
    multisegment_tables = [
        chunk
        for chunk in chunks
        if chunk.get("chunk_type") == "table"
        and table_segment_counts.get(chunk.get("table_id"), 0) > 1
    ]

    targeted_ids = {
        str(chunk.get("chunk_id") or "")
        for chunk in [
            *attachment_tables,
            *formula_linked,
            *multisegment_tables,
        ]
    }

    suspicious_pool = [
        chunk
        for chunk in chunks
        if chunk.get("chunk_type") != "fallback_segment"
        and flags_by_id.get(str(chunk.get("chunk_id") or ""))
        and str(chunk.get("chunk_id") or "") not in previous_ids
    ]
    suspicious_pool.sort(
        key=lambda chunk: (
            -severity(flags_by_id[str(chunk.get("chunk_id") or "")]),
            str(chunk.get("chunk_key") or ""),
        )
    )
    suspicious = suspicious_pool[: args.suspicious_limit]

    suspicious_ids = {
        str(chunk.get("chunk_id") or "")
        for chunk in suspicious
    }

    control_excluded = (
        fallback_ids | targeted_ids | suspicious_ids | previous_ids
    )
    controls = sample_controls(
        chunks=chunks,
        excluded_ids=control_excluded,
        seed=args.seed,
    )

    selected_with_category: list[tuple[str, dict[str, Any]]] = []
    selected_with_category.extend(("fallback_all", chunk) for chunk in fallback)
    selected_with_category.extend(
        ("multisegment_table_all", chunk)
        for chunk in multisegment_tables
    )
    selected_with_category.extend(
        ("formula_link_all", chunk) for chunk in formula_linked
    )
    selected_with_category.extend(
        ("attachment_table_all", chunk) for chunk in attachment_tables
    )
    selected_with_category.extend(
        ("suspicious_non_fallback", chunk) for chunk in suspicious
    )
    selected_with_category.extend(("stratified_control", chunk) for chunk in controls)

    # Final de-duplication while keeping priority order.
    seen: set[str] = set()
    items: list[dict[str, Any]] = []

    for category, chunk in selected_with_category:
        chunk_id = str(chunk.get("chunk_id") or "")
        if not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)

        items.append(
            {
                "review_no": len(items) + 1,
                "category": category,
                "risk_flags": flags_by_id.get(chunk_id, []),
                "chunk": chunk,
                "source_units": source_units_for_chunk(
                    chunk, units_by_article
                ),
                "canonical_article": articles.get(
                    str(
                        chunk.get("parent_article_id")
                        or chunk.get("article_id")
                        or ""
                    ),
                    {},
                ),
                "canonical_table": tables.get(
                    str(chunk.get("table_id") or ""),
                    {},
                ),
            }
        )

    category_counts = Counter(item["category"] for item in items)
    selected_type_counts = Counter(
        item["chunk"].get("chunk_type") for item in items
    )
    selected_source_counts = Counter(
        item["chunk"].get("source_type") for item in items
    )
    flag_counts = Counter(
        flag
        for item in items
        for flag in item["risk_flags"]
    )
    selected_ids = {
        str(item["chunk"].get("chunk_id") or "") for item in items
    }

    summary = {
        "seed": args.seed,
        "corpus_article_count": len(articles),
        "corpus_chunk_count": len(chunks),
        "corpus_chunk_type_counts": dict(
            Counter(chunk.get("chunk_type") for chunk in chunks)
        ),
        "selected_count": len(items),
        "fallback_selected": category_counts["fallback_all"],
        "attachment_table_chunk_count": len(attachment_tables),
        "attachment_table_selected": sum(
            str(chunk.get("chunk_id") or "") in selected_ids
            for chunk in attachment_tables
        ),
        "formula_linked_chunk_count": len(formula_linked),
        "formula_linked_selected": sum(
            str(chunk.get("chunk_id") or "") in selected_ids
            for chunk in formula_linked
        ),
        "multisegment_table_chunk_count": len(multisegment_tables),
        "multisegment_table_selected": sum(
            str(chunk.get("chunk_id") or "") in selected_ids
            for chunk in multisegment_tables
        ),
        "suspicious_selected": category_counts["suspicious_non_fallback"],
        "control_selected": category_counts["stratified_control"],
        "selected_type_counts": dict(selected_type_counts),
        "selected_source_counts": dict(selected_source_counts),
        "automated_flag_counts": dict(flag_counts),
        "previous_review_file": str(args.previous_review),
        "previous_review_id_count": len(previous_ids),
        "selected_chunk_ids": [
            item["chunk"].get("chunk_id") for item in items
        ],
        "selection_policy": {
            "fallback": "all fallback_segment chunks",
            "targeted": (
                "all attachment table chunks, all formula-linked chunks, "
                "and all multi-segment table chunks"
            ),
            "suspicious": (
                f"top {args.suspicious_limit} non-fallback chunks by "
                "deterministic anomaly severity, excluding previous review IDs"
            ),
            "controls": {
                "method": "fixed-seed stratified random sample",
                "targets": DEFAULT_CONTROL_TARGETS,
                "previous_review_ids_excluded": True,
            },
        },
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)

    summary_path = args.output_dir / "chunk_quality_audit_summary.json"
    items_path = args.output_dir / "chunk_quality_review_items.json"
    csv_path = args.output_dir / "chunk_quality_review.csv"
    markdown_path = args.output_dir / "chunk_quality_review.md"

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    items_path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(csv_path, items)
    write_markdown(markdown_path, items, summary)

    print("CHUNK_QUALITY_AUDIT_PREPARED")
    print("Corpus articles:", len(articles))
    print("Corpus chunks:", len(chunks))
    print("Selected:", len(items))
    print("Fallback selected:", category_counts["fallback_all"])
    print(
        "Attachment table chunks selected:",
        summary["attachment_table_selected"],
        "/",
        summary["attachment_table_chunk_count"],
    )
    print(
        "Formula-linked chunks selected:",
        summary["formula_linked_selected"],
        "/",
        summary["formula_linked_chunk_count"],
    )
    print(
        "Multi-segment table chunks selected:",
        summary["multisegment_table_selected"],
        "/",
        summary["multisegment_table_chunk_count"],
    )
    print(
        "Suspicious non-fallback selected:",
        category_counts["suspicious_non_fallback"],
    )
    print("Controls selected:", category_counts["stratified_control"])
    print("Previous review IDs:", len(previous_ids))
    print("Selected types:", dict(selected_type_counts))
    print("Automated flags:", dict(flag_counts))
    print("Summary:", summary_path)
    print("Review items:", items_path)
    print("CSV:", csv_path)
    print("Markdown:", markdown_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
