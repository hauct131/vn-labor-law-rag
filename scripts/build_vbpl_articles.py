"""Adapter: VBPL snapshots → vbpl_articles_raw.json (corpus schema).

Usage (defaults):
    python scripts/build_vbpl_articles.py --strict

Full usage:
    python scripts/build_vbpl_articles.py \\
        --run-manifest data/raw/vbpl/run_manifest.json \\
        --config       config/vbpl_corpus.json \\
        --output       data/processed/vbpl_articles_raw.json \\
        --report       data/quality/vbpl_article_build_report.json \\
        --strict
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ADAPTER_VERSION = "vbpl-article-adapter-v1"

# ── Regex patterns ─────────────────────────────────────────────────────────────

# Heading Điều at START of line only (capturing group 1 = article number as int string)
_DIEU_HEADING = re.compile(
    r"^Điều\s+(\d+)\s*[.．]\s*(.*?)$",
    re.MULTILINE,
)

# Clause: digit(s) followed by "." at start of line — but NOT decimals, years, money
# Rejects:  "2020.", "100%.", floats "1.5.", references "khoản 1"
_CLAUSE_HEADING = re.compile(
    r"^(\d{1,3})\.\s",
    re.MULTILINE,
)

# Point: Vietnamese legal point labels at start of line
# Supports: a) b) c) d) đ) e) g) h) i) k) l) m) n) o) p) q) r) s) t) u) v) x) y)
_POINT_HEADING = re.compile(
    r"^([a-zđ])\)\s",
    re.MULTILINE,
)


# ── include_articles parser ─────────────────────────────────────────────────────

def parse_include_articles(spec: str) -> set[int]:
    """Parse 'include_articles' field into a set of article numbers.

    Supports:
        "1-9"    → {1,2,...,9}
        "67-81"  → {67,...,81}
        "7"      → {7}
        "4,6"    → {4, 6}
        "1-220"  → {1,...,220}
    """
    result: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            result.update(range(int(lo), int(hi) + 1))
        else:
            result.add(int(part))
    return result


# ── Article heading parser ──────────────────────────────────────────────────────

def find_article_headings(text: str) -> list[tuple[int, str, int]]:
    """Find all Điều headings at start of line.

    Returns list of (article_number, title, char_offset).
    """
    headings = []
    for m in _DIEU_HEADING.finditer(text):
        num = int(m.group(1))
        title = m.group(2).strip()
        headings.append((num, title, m.start()))
    return headings


def select_main_sequence(
    headings: list[tuple[int, str, int]],
    include_set: set[int],
) -> list[tuple[int, str, int]]:
    """Pick the first contiguous run that satisfies include_set.

    Strategy:
    1. Walk headings in order.
    2. For each heading whose number is in include_set and not yet seen,
       add to the sequence.
    3. Stop once we have all required articles.
    4. Duplicate headings (appendix/reference repeats) are ignored.

    Returns ordered list of (num, title, offset) for the main sequence only.
    """
    seen: set[int] = set()
    sequence: list[tuple[int, str, int]] = []
    for num, title, offset in headings:
        if num in include_set and num not in seen:
            seen.add(num)
            sequence.append((num, title, offset))
    return sequence


# ── Content unit parser ─────────────────────────────────────────────────────────

def _make_unit_id(doc_id: str, article_num: int, unit_type: str, index: int) -> str:
    """Stable unit_id following the pattern in the task spec."""
    base = f"vbpl:{doc_id}:article:{article_num}"
    if unit_type == "preamble":
        return f"{base}|preamble={index}"
    elif unit_type == "clause":
        return f"{base}|clause={index}"
    elif unit_type == "point":
        # index here is (clause_num, point_label)
        return f"{base}|clause={index[0]}|point={index[1]}"
    elif unit_type == "clause_continuation":
        return f"{base}|clause_continuation={index}"
    else:
        return f"{base}|unit={unit_type}={index}"


def parse_content_units(
    article_text: str,
    doc_id: str,
    article_num: int,
) -> list[dict]:
    """Split article body into content_units matching chunker schema.

    unit_types produced: preamble, clause, point, clause_continuation

    Rules:
    - Text before first clause → preamble
    - Lines starting with "N." (N=1..999) followed by space → clause
    - Lines starting with "x)" (x=a-z,đ) → point (attached to active clause)
    - Text between clause heading and next point/clause → clause body (stored on clause)
    - Continuation text after points → clause_continuation
    """
    units: list[dict] = []

    # Find clause split points
    clause_matches = list(_CLAUSE_HEADING.finditer(article_text))
    point_matches = list(_POINT_HEADING.finditer(article_text))

    # Build sorted event list: (offset, type, match)
    events: list[tuple[int, str, Any]] = []
    for m in clause_matches:
        # Guard: reject false positives — year-like numbers ≥ 1900, etc.
        num = int(m.group(1))
        if num >= 200:  # clauses never exceed ~30 in Vietnamese law
            continue
        events.append((m.start(), "clause", m))
    for m in point_matches:
        events.append((m.start(), "point", m))

    events.sort(key=lambda e: e[0])

    if not events:
        # No clause structure — entire text is preamble
        text = article_text.strip()
        if text:
            uid = _make_unit_id(doc_id, article_num, "preamble", 1)
            units.append({
                "unit_id": uid,
                "unit_type": "preamble",
                "text": text,
            })
        return units

    # Preamble: text before first event
    first_offset = events[0][0]
    preamble_text = article_text[:first_offset].strip()
    if preamble_text:
        uid = _make_unit_id(doc_id, article_num, "preamble", 1)
        units.append({
            "unit_id": uid,
            "unit_type": "preamble",
            "text": preamble_text,
        })

    # Parse events into clause/point groups
    active_clause_num: str | None = None
    clause_counter = 0
    point_counter: dict[str, int] = {}  # clause_num → count within clause
    continuation_counter = 0

    for i, (offset, etype, match) in enumerate(events):
        # Determine end of this segment
        next_offset = events[i + 1][0] if i + 1 < len(events) else len(article_text)
        segment_text = article_text[offset:next_offset].strip()

        if etype == "clause":
            num_str = match.group(1)
            active_clause_num = num_str
            clause_counter += 1
            point_counter[num_str] = 0

            uid = _make_unit_id(doc_id, article_num, "clause", int(num_str))
            units.append({
                "unit_id": uid,
                "unit_type": "clause",
                "clause_number": num_str,
                "text": segment_text,
            })

        elif etype == "point":
            label = match.group(1)
            if active_clause_num is None:
                # Orphan point — treat as preamble continuation
                uid = _make_unit_id(doc_id, article_num, "preamble", len(units) + 1)
                units.append({
                    "unit_id": uid,
                    "unit_type": "preamble",
                    "text": segment_text,
                })
                continue

            point_counter[active_clause_num] = point_counter.get(active_clause_num, 0) + 1
            uid = _make_unit_id(
                doc_id, article_num, "point",
                (int(active_clause_num), label)
            )
            units.append({
                "unit_id": uid,
                "unit_type": "point",
                "clause_number": active_clause_num,
                "point_label": label,
                "text": segment_text,
            })

    return units


# ── Article builder ─────────────────────────────────────────────────────────────

def _stable_article_id(canonical_doc_id: str, article_num: int) -> str:
    """Stable article_id: no timestamp, derived from doc + number."""
    return f"{canonical_doc_id}:article:{article_num}"


def build_articles_from_snapshot(
    snapshot_dir: Path,
    config_doc: dict,
    include_set: set[int],
) -> tuple[list[dict], list[str]]:
    """Parse one VBPL snapshot → list of article dicts + warnings.

    Returns (articles, warnings).
    """
    warnings: list[str] = []

    # Read files
    full_text_path = snapshot_dir / "full_text.txt"
    manifest_path = snapshot_dir / "manifest.json"
    sums_path = snapshot_dir / "SHA256SUMS.txt"

    for p in (full_text_path, manifest_path):
        if not p.exists():
            raise FileNotFoundError(f"Missing required file: {p}")

    full_text = full_text_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Read sha256 for full_text.txt
    source_sha256: str | None = manifest.get("content_hashes", {}).get("full_text_text_sha256")

    # Validate document number
    doc_meta = manifest.get("document", {})
    manifest_doc_num = doc_meta.get("document_number", "")
    config_doc_num = config_doc["document_number"]
    if manifest_doc_num != config_doc_num:
        raise ValueError(
            f"Document number mismatch: manifest={manifest_doc_num!r} "
            f"config={config_doc_num!r}"
        )

    # Validate item_id
    manifest_item_id = doc_meta.get("item_id", "")
    config_item_id = config_doc.get("item_id", "")
    if config_item_id and manifest_item_id != config_item_id:
        raise ValueError(
            f"ItemID mismatch for {config_doc_num}: "
            f"manifest={manifest_item_id!r} config={config_item_id!r}"
        )

    # Source metadata from manifest
    source_info = manifest.get("source", {})
    detail_url = source_info.get("detail_url") or source_info.get("gateway_url")
    source_urls: list[str] = [detail_url] if detail_url else []
    retrieved_at: str | None = manifest.get("retrieved_at")
    issued_at: str | None = doc_meta.get("issued_at")
    effective_from: str | None = doc_meta.get("effective_from")
    effective_to: str | None = doc_meta.get("effective_to")
    issuing_authority: str | None = doc_meta.get("issuing_authority")
    doc_title: str | None = doc_meta.get("title")

    canonical_doc_id = config_doc["canonical_document_id"]

    # Find all article headings
    headings = find_article_headings(full_text)
    main_seq = select_main_sequence(headings, include_set)

    found_nums = {num for num, _, _ in main_seq}
    missing = sorted(include_set - found_nums)
    if missing:
        raise ValueError(
            f"{config_doc_num}: Missing articles {missing} in full_text.txt"
        )

    # Build articles
    articles: list[dict] = []

    # Create index for slicing text
    all_offsets = [(num, title, offset) for num, title, offset in main_seq]

    for idx, (num, title, start_offset) in enumerate(all_offsets):
        # Find end: start of next article in main sequence, or EOF
        if idx + 1 < len(all_offsets):
            end_offset = all_offsets[idx + 1][2]
        else:
            end_offset = len(full_text)

        # Article body = text after heading line
        heading_line_end = full_text.index("\n", start_offset) + 1
        article_body = full_text[heading_line_end:end_offset]

        article_id = _stable_article_id(canonical_doc_id, num)

        content_units = parse_content_units(
            article_body,
            doc_id=canonical_doc_id,
            article_num=num,
        )

        # Reconstruct full heading string
        heading = f"Điều {num}. {title}"

        article = {
            # ── IDs ─────────────────────────────────────────────────────
            "article_id": article_id,
            "article_code": str(num),
            "article_number": num,
            "article_title": title,
            "heading": heading,
            "document_id": canonical_doc_id,
            "source_document_id": canonical_doc_id,
            "document_number": config_doc_num,
            "source_item_id": manifest_item_id or None,
            # ── Source provenance ────────────────────────────────────────
            "source_adapter": "vbpl",
            "corpus_role": "canonical",
            "source_urls": source_urls,
            "source_sha256": source_sha256,
            "retrieved_at": retrieved_at,
            "issued_at": issued_at,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "issuing_authority": issuing_authority,
            "document_title": doc_title,
            # ── Chunker-required fields ───────────────────────────────────
            "topic_code": None,
            "topic_name": None,
            "codification_code": None,
            "chapter": None,
            "section": None,
            "source_type": config_doc.get("type_vb_code") or doc_meta.get("type_vb", {}).get("code"),
            "source_note_text": None,
            "parser_version": ADAPTER_VERSION,
            "relations": [],
            "attachments": [],
            "tables": [],
            # ── Content ──────────────────────────────────────────────────
            "content_units": content_units,
        }
        articles.append(article)

    return articles, warnings


# ── Atomic write ────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_" + path.name + "_")
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ── Main logic ──────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_vbpl_articles",
        description="Convert VBPL snapshots to canonical articles_raw schema.",
    )
    p.add_argument(
        "--run-manifest",
        default="data/raw/vbpl/run_manifest.json",
        metavar="PATH",
    )
    p.add_argument(
        "--config",
        default="config/vbpl_corpus.json",
        metavar="PATH",
    )
    p.add_argument(
        "--output",
        default="data/processed/vbpl_articles_raw.json",
        metavar="PATH",
    )
    p.add_argument(
        "--report",
        default="data/quality/vbpl_article_build_report.json",
        metavar="PATH",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any document fails or article counts mismatch.",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    run_manifest_path = Path(args.run_manifest)
    config_path = Path(args.config)
    output_path = Path(args.output)
    report_path = Path(args.report)

    # ── Load inputs ──────────────────────────────────────────────────────────
    if not run_manifest_path.exists():
        logger.error("run_manifest not found: %s", run_manifest_path)
        return 2
    if not config_path.exists():
        logger.error("config not found: %s", config_path)
        return 2

    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))

    # Build lookup: document_number → config entry
    config_by_number: dict[str, dict] = {
        d["document_number"]: d for d in config["documents"]
    }

    # Only process snapshots listed in run_manifest results
    manifest_results = run_manifest.get("results", [])

    documents_expected = len(manifest_results)
    documents_processed = 0
    articles_expected_total = 0
    articles_created = 0
    missing_documents: list[str] = []
    missing_articles: list[str] = []
    duplicate_article_ids: list[str] = []
    empty_articles: list[str] = []
    all_warnings: list[str] = []

    all_articles: list[dict] = []
    seen_ids: set[str] = set()

    for result in manifest_results:
        doc_num = result["document_number"]
        snapshot_dir_str = result["snapshot_dir"]
        snapshot_dir = Path(snapshot_dir_str)

        if doc_num not in config_by_number:
            logger.warning("Document %s not in config; skipping.", doc_num)
            missing_documents.append(doc_num)
            continue

        config_doc = config_by_number[doc_num]
        expected_n = config_doc.get("expected_articles", 0)
        include_set = set(range(1, expected_n + 1))  # parse all N expected articles

        articles_expected_total += expected_n

        if not snapshot_dir.exists():
            logger.error("Snapshot dir not found: %s", snapshot_dir)
            missing_documents.append(doc_num)
            if args.strict:
                return 1
            continue

        try:
            articles, warns = build_articles_from_snapshot(
                snapshot_dir, config_doc, include_set
            )
            all_warnings.extend(warns)
        except Exception as exc:
            logger.error("Failed to process %s: %s", doc_num, exc)
            missing_documents.append(doc_num)
            if args.strict:
                return 1
            continue

        documents_processed += 1

        # Check for empty articles
        for art in articles:
            if not art.get("content_units"):
                empty_articles.append(art["article_id"])
                logger.warning("Empty article: %s", art["article_id"])

        # Check for duplicates
        for art in articles:
            aid = art["article_id"]
            if aid in seen_ids:
                duplicate_article_ids.append(aid)
                logger.error("Duplicate article_id: %s", aid)
            else:
                seen_ids.add(aid)
                all_articles.append(art)
                articles_created += 1

    # ── Compute articles_expected from config (not hard-coded) ───────────────
    # articles_expected_total already computed above per include_set

    # ── Check missing articles ────────────────────────────────────────────────
    if articles_created != articles_expected_total:
        msg = (
            f"Article count mismatch: expected={articles_expected_total} "
            f"created={articles_created}"
        )
        logger.warning(msg)
        all_warnings.append(msg)

    # ── Build output corpus ───────────────────────────────────────────────────
    corpus = {
        "metadata": {
            "adapter_version": ADAPTER_VERSION,
            "run_id": run_manifest.get("run_id"),
            "schema_version": "vbpl-corpus-v1",
            "documents_expected": documents_expected,
            "documents_processed": documents_processed,
            "articles_expected": articles_expected_total,
            "articles_created": articles_created,
            "missing_documents": missing_documents,
            "missing_articles": missing_articles,
            "duplicate_article_ids": duplicate_article_ids,
            "empty_articles": empty_articles,
        },
        "articles": all_articles,
    }

    # ── Strict checks ─────────────────────────────────────────────────────────
    strict_failures: list[str] = []
    if missing_documents:
        strict_failures.append(f"missing_documents={missing_documents}")
    if duplicate_article_ids:
        strict_failures.append(f"duplicate_article_ids={duplicate_article_ids}")
    if articles_created != articles_expected_total:
        strict_failures.append(
            f"article_count_mismatch: expected={articles_expected_total} "
            f"created={articles_created}"
        )

    # ── Write output ──────────────────────────────────────────────────────────
    output_text = json.dumps(corpus, ensure_ascii=False, indent=2) + "\n"
    try:
        _atomic_write(output_path, output_text)
        logger.info("Wrote %d articles to %s", articles_created, output_path)
    except Exception as exc:
        logger.error("Failed to write output: %s", exc)
        return 1

    # ── Write report ──────────────────────────────────────────────────────────
    report = {
        "adapter_version": ADAPTER_VERSION,
        "documents_expected": documents_expected,
        "documents_processed": documents_processed,
        "articles_expected": articles_expected_total,
        "articles_created": articles_created,
        "missing_documents": missing_documents,
        "missing_articles": missing_articles,
        "duplicate_article_ids": duplicate_article_ids,
        "empty_articles": empty_articles,
        "warnings": all_warnings,
        "strict_failures": strict_failures,
        "strict_passed": len(strict_failures) == 0,
    }
    try:
        _atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        logger.info("Wrote report to %s", report_path)
    except Exception as exc:
        logger.error("Failed to write report: %s", exc)

    # ── Final exit ────────────────────────────────────────────────────────────
    logger.info(
        "Done: docs=%d/%d articles=%d/%d strict_failures=%d",
        documents_processed, documents_expected,
        articles_created, articles_expected_total,
        len(strict_failures),
    )

    if args.strict and strict_failures:
        for f in strict_failures:
            logger.error("STRICT FAIL: %s", f)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
