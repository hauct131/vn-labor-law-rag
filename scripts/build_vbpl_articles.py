"""Adapter: VBPL snapshots → vbpl_articles_raw.json (corpus schema).

Two-phase approach per document:
  Phase 1 (validate): parse and verify one global main sequence of ALL expected_articles (1..N).
  Phase 2 (select):   slice articles from the global main sequence and output only the subset defined by include_articles.

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
import bisect
import json
import logging
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ADAPTER_VERSION = "vbpl-article-adapter-v1"

# ── Regex patterns ──────────────────────────────────────────────────────────────

# Heading Điều at START of line only
_DIEU_HEADING = re.compile(
    r"^Điều\s+(\d+)\s*[.．]\s*(.*?)$",
    re.MULTILINE,
)

# Clause: digit(s) followed by "." at start of line (rejects numbers >= 200)
_CLAUSE_HEADING = re.compile(r"^(\d{1,3})\.\s", re.MULTILINE)

# Point: lowercase letter or đ + ")" + space at start of line
_POINT_HEADING = re.compile(r"^([a-zđ])\)\s", re.MULTILINE)


# ── include_articles parser ─────────────────────────────────────────────────────

def parse_include_articles(spec: str) -> set[int]:
    """Parse 'include_articles' spec into a set of article numbers."""
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


# ── Article heading finder ──────────────────────────────────────────────────────

def find_article_headings(text: str) -> list[tuple[int, str, int]]:
    """Find all Điều headings at start of line.

    Returns list of (article_number, title, char_offset).
    """
    headings = []
    for m in _DIEU_HEADING.finditer(text):
        headings.append((int(m.group(1)), m.group(2).strip(), m.start()))
    return headings


# ── Global sequence selection algorithm ─────────────────────────────────────────

def select_content_sequence(
    headings: list[tuple[int, str, int]],
    needed: set[int],
    full_text: str = "",
) -> list[tuple[int, str, int]]:
    """Find the single global main sequence of articles matching needed numbers.

    Evaluates candidate chains (sequences starting at each occurrence of the initial needed number
    and stepping through subsequent needed numbers in ascending offset order).
    Scores candidate chains based on body character length, clause count, absence of empty articles,
    and lack of form placeholder dots.

    Returns ordered list of (article_number, title, offset).
    """
    if not needed:
        return []

    sorted_needed = sorted(needed)
    first_num = sorted_needed[0]

    # Find all occurrences of first_num
    first_num_indices = [i for i, h in enumerate(headings) if h[0] == first_num]

    if not first_num_indices:
        # Fallback: simple first occurrence of whatever is present
        seen: set[int] = set()
        res: list[tuple[int, str, int]] = []
        for num, title, off in headings:
            if num in needed and num not in seen:
                seen.add(num)
                res.append((num, title, off))
        return res

    all_offs_sorted = sorted(h[2] for h in headings)

    def _next_heading_offset(off: int) -> int:
        idx = bisect.bisect_right(all_offs_sorted, off)
        return all_offs_sorted[idx] if idx < len(all_offs_sorted) else len(full_text)

    candidate_chains: list[list[tuple[int, str, int]]] = []

    for start_idx in first_num_indices:
        chain: list[tuple[int, str, int]] = [headings[start_idx]]
        curr_pos = headings[start_idx][2]
        valid_chain = True

        for k in sorted_needed[1:]:
            # Find the FIRST occurrence of k appearing after curr_pos
            match = next((h for h in headings if h[0] == k and h[2] > curr_pos), None)
            if match is None:
                valid_chain = False
                break
            chain.append(match)
            curr_pos = match[2]

        if valid_chain and len(chain) == len(sorted_needed):
            candidate_chains.append(chain)

    if not candidate_chains:
        # Fallback greedy sequence
        seen_fallback: set[int] = set()
        fallback_res: list[tuple[int, str, int]] = []
        for num, title, off in headings:
            if num in needed and num not in seen_fallback:
                seen_fallback.add(num)
                fallback_res.append((num, title, off))
        return fallback_res

    if len(candidate_chains) == 1 or not full_text:
        return candidate_chains[0]

    # Score candidates if multiple exist
    def _score_chain(chain: list[tuple[int, str, int]]) -> tuple[bool, int, int]:
        total_len = 0
        total_clauses = 0
        empty_count = 0
        dot_count = 0

        for i, (num, title, off) in enumerate(chain):
            nxt = chain[i + 1][2] if i + 1 < len(chain) else _next_heading_offset(off)
            line_end = full_text.find("\n", off)
            line_end = (line_end + 1) if line_end != -1 else off
            body = full_text[line_end:nxt].strip()

            if not body:
                empty_count += 1
            total_len += len(body)
            total_clauses += len(_CLAUSE_HEADING.findall(body))
            dot_count += body.count(".....")

        effective_len = max(0, total_len - dot_count * 5)
        return (empty_count == 0, total_clauses, effective_len)

    candidate_chains.sort(key=lambda c: _score_chain(c), reverse=True)
    return candidate_chains[0]


def select_main_sequence(
    headings: list[tuple[int, str, int]],
    needed_set: set[int],
    full_text: str = "",
) -> list[tuple[int, str, int]]:
    """Backward-compatible wrapper for unit tests and internal calls."""
    return select_content_sequence(headings, needed_set, full_text=full_text)


# ── Content unit parser ─────────────────────────────────────────────────────────

def _make_unit_id(doc_id: str, article_num: int, unit_type: str, index: Any) -> str:
    """Stable unit_id generation."""
    base = f"vbpl:{doc_id}:article:{article_num}"
    if unit_type == "preamble":
        return f"{base}|preamble={index}"
    if unit_type == "clause":
        return f"{base}|clause={index}"
    if unit_type == "point":
        clause_n, label = index
        return f"{base}|clause={clause_n}|point={label}"
    if unit_type == "clause_continuation":
        return f"{base}|clause_continuation={index}"
    return f"{base}|unit={unit_type}={index}"


def parse_content_units(article_text: str, doc_id: str, article_num: int) -> list[dict]:
    """Split article body into preamble/clause/point content units."""
    units: list[dict] = []
    clause_matches = list(_CLAUSE_HEADING.finditer(article_text))
    point_matches = list(_POINT_HEADING.finditer(article_text))

    events: list[tuple[int, str, Any]] = []
    for m in clause_matches:
        n = int(m.group(1))
        if n >= 200:  # ignore year-like numbers
            continue
        events.append((m.start(), "clause", m))
    for m in point_matches:
        events.append((m.start(), "point", m))

    events.sort(key=lambda e: e[0])

    if not events:
        text = article_text.strip()
        if text:
            units.append({
                "unit_id": _make_unit_id(doc_id, article_num, "preamble", 1),
                "unit_type": "preamble",
                "text": text,
            })
        return units

    # Preamble text before first clause/point
    preamble_text = article_text[:events[0][0]].strip()
    if preamble_text:
        units.append({
            "unit_id": _make_unit_id(doc_id, article_num, "preamble", 1),
            "unit_type": "preamble",
            "text": preamble_text,
        })

    active_clause: str | None = None

    for i, (offset, etype, match) in enumerate(events):
        next_off = events[i + 1][0] if i + 1 < len(events) else len(article_text)
        seg = article_text[offset:next_off].strip()

        if etype == "clause":
            num_str = match.group(1)
            active_clause = num_str
            units.append({
                "unit_id": _make_unit_id(doc_id, article_num, "clause", int(num_str)),
                "unit_type": "clause",
                "clause_number": num_str,
                "text": seg,
            })

        elif etype == "point":
            label = match.group(1)
            if active_clause is None:
                units.append({
                    "unit_id": _make_unit_id(doc_id, article_num, "preamble", len(units) + 1),
                    "unit_type": "preamble",
                    "text": seg,
                })
                continue
            units.append({
                "unit_id": _make_unit_id(
                    doc_id, article_num, "point", (int(active_clause), label)
                ),
                "unit_type": "point",
                "clause_number": active_clause,
                "point_label": label,
                "text": seg,
            })

    return units


# ── Article builder ─────────────────────────────────────────────────────────────

def _stable_article_id(canonical_doc_id: str, article_num: int) -> str:
    return f"{canonical_doc_id}:article:{article_num}"


def build_articles_from_snapshot(
    snapshot_dir: Path,
    config_doc: dict,
    validation_set: set[int],
    selection_set: set[int] | None = None,
) -> tuple[list[dict], int, list[str]]:
    """Parse one VBPL snapshot → (selected_articles, validated_count, warnings)."""
    if selection_set is None:
        selection_set = validation_set

    warnings: list[str] = []

    full_text_path = snapshot_dir / "full_text.txt"
    manifest_path = snapshot_dir / "manifest.json"

    for p in (full_text_path, manifest_path):
        if not p.exists():
            raise FileNotFoundError(f"Missing required file: {p}")

    full_text = full_text_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    source_sha256: str | None = manifest.get("content_hashes", {}).get("full_text_text_sha256")

    doc_meta = manifest.get("document", {})
    manifest_doc_num = doc_meta.get("document_number", "")
    config_doc_num = config_doc["document_number"]
    if manifest_doc_num != config_doc_num:
        raise ValueError(
            f"Document number mismatch: manifest={manifest_doc_num!r} "
            f"config={config_doc_num!r}"
        )

    manifest_item_id = doc_meta.get("item_id", "")
    config_item_id = config_doc.get("item_id", "")
    if config_item_id and manifest_item_id != config_item_id:
        raise ValueError(
            f"ItemID mismatch for {config_doc_num}: "
            f"manifest={manifest_item_id!r} config={config_item_id!r}"
        )

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
    item_id_str = manifest_item_id or config_item_id or ""
    source_document_id = f"vbpl:item:{item_id_str}" if item_id_str else canonical_doc_id

    # ── Phase 1: Global main sequence detection & validation ─────────────────
    headings = find_article_headings(full_text)
    main_seq = select_content_sequence(headings, validation_set, full_text)

    found_nums = {num for num, _, _ in main_seq}
    missing = sorted(validation_set - found_nums)
    if missing:
        raise ValueError(
            f"{config_doc_num}: Missing expected articles {missing} in full_text "
            f"(found {len(found_nums)}/{len(validation_set)})"
        )
    validated_count = len(found_nums)

    # ── Phase 2: Slice all articles in the global main sequence ──────────────
    all_offs_sorted = sorted(h[2] for h in headings)

    def _next_heading_offset_in_doc(off: int) -> int:
        idx = bisect.bisect_right(all_offs_sorted, off)
        return all_offs_sorted[idx] if idx < len(all_offs_sorted) else len(full_text)

    all_articles_by_num: dict[int, dict] = {}

    for idx, (num, title, start_offset) in enumerate(main_seq):
        if idx + 1 < len(main_seq):
            end_offset = main_seq[idx + 1][2]
        else:
            end_offset = _next_heading_offset_in_doc(start_offset)

        heading_line_end = full_text.find("\n", start_offset)
        heading_line_end = (heading_line_end + 1) if heading_line_end != -1 else start_offset

        article_body = full_text[heading_line_end:end_offset]
        article_id = _stable_article_id(canonical_doc_id, num)

        content_units = parse_content_units(
            article_body, doc_id=canonical_doc_id, article_num=num
        )

        all_articles_by_num[num] = {
            "article_id": article_id,
            "article_code": str(num),
            "article_number": num,
            "article_title": title,
            "heading": f"Điều {num}. {title}",
            "document_id": canonical_doc_id,
            "source_document_id": source_document_id,
            "document_number": config_doc_num,
            "source_item_id": manifest_item_id or None,
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
            "topic_code": None,
            "topic_name": None,
            "codification_code": None,
            "chapter": None,
            "section": None,
            "source_type": (
                config_doc.get("type_vb_code")
                or doc_meta.get("type_vb", {}).get("code")
            ),
            "source_note_text": None,
            "parser_version": ADAPTER_VERSION,
            "relations": [],
            "attachments": [],
            "tables": [],
            "content_units": content_units,
        }

    # Filter to selection_set
    selected_articles = [
        all_articles_by_num[num]
        for num in sorted(selection_set)
        if num in all_articles_by_num
    ]

    return selected_articles, validated_count, warnings


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


# ── CLI ─────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_vbpl_articles",
        description="Convert VBPL snapshots to canonical vbpl_articles_raw.json.",
    )
    p.add_argument("--run-manifest", default="data/raw/vbpl/run_manifest.json", metavar="PATH")
    p.add_argument("--config",       default="config/vbpl_corpus.json",          metavar="PATH")
    p.add_argument("--output",       default="data/processed/vbpl_articles_raw.json", metavar="PATH")
    p.add_argument("--report",       default="data/quality/vbpl_article_build_report.json", metavar="PATH")
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero when any document fails or counts mismatch.")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
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
    config_path       = Path(args.config)
    output_path       = Path(args.output)
    report_path       = Path(args.report)

    if not run_manifest_path.exists():
        logger.error("run_manifest not found: %s", run_manifest_path)
        return 2
    if not config_path.exists():
        logger.error("config not found: %s", config_path)
        return 2

    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))

    config_by_number: dict[str, dict] = {d["document_number"]: d for d in config["documents"]}
    manifest_results = run_manifest.get("results", [])

    documents_expected       = len(manifest_results)
    documents_processed      = 0
    articles_validated_total = 0
    articles_selected_total  = 0
    articles_created         = 0
    missing_documents:        list[str] = []
    missing_articles:         list[str] = []
    duplicate_article_ids:    list[str] = []
    empty_articles:           list[str] = []
    all_warnings:             list[str] = []
    all_articles:             list[dict] = []
    seen_ids:                 set[str] = set()

    for result in manifest_results:
        doc_num      = result["document_number"]
        snapshot_dir = Path(result["snapshot_dir"])

        if doc_num not in config_by_number:
            logger.warning("Document %s not in config; skipping.", doc_num)
            missing_documents.append(doc_num)
            continue

        config_doc   = config_by_number[doc_num]
        expected_n   = config_doc.get("expected_articles", 0)
        include_spec = config_doc.get("include_articles", "")

        validation_set = set(range(1, expected_n + 1))
        selection_set  = (
            parse_include_articles(include_spec) if include_spec else validation_set
        )

        articles_validated_total += expected_n
        articles_selected_total  += len(selection_set)

        if not snapshot_dir.exists():
            logger.error("Snapshot dir not found: %s", snapshot_dir)
            missing_documents.append(doc_num)
            if args.strict:
                return 1
            continue

        try:
            articles, validated_count, warns = build_articles_from_snapshot(
                snapshot_dir, config_doc, validation_set, selection_set
            )
            all_warnings.extend(warns)
        except Exception as exc:
            logger.error("Failed to process %s: %s", doc_num, exc)
            missing_documents.append(doc_num)
            if args.strict:
                return 1
            continue

        documents_processed += 1

        for art in articles:
            if not art.get("content_units"):
                empty_articles.append(art["article_id"])
                logger.warning("Empty article: %s", art["article_id"])

        for art in articles:
            aid = art["article_id"]
            if aid in seen_ids:
                duplicate_article_ids.append(aid)
                logger.error("Duplicate article_id: %s", aid)
            else:
                seen_ids.add(aid)
                all_articles.append(art)
                articles_created += 1

    articles_excluded = articles_validated_total - articles_selected_total

    if articles_created != articles_selected_total:
        msg = (
            f"Selected count mismatch: "
            f"expected={articles_selected_total} created={articles_created}"
        )
        logger.warning(msg)
        all_warnings.append(msg)

    corpus = {
        "metadata": {
            "adapter_version":           ADAPTER_VERSION,
            "run_id":                    run_manifest.get("run_id"),
            "schema_version":            "vbpl-corpus-v1",
            "documents_expected":        documents_expected,
            "documents_processed":       documents_processed,
            "articles_validated_total":  articles_validated_total,
            "articles_selected":         articles_created,
            "articles_excluded_by_scope": articles_excluded,
            "missing_documents":         missing_documents,
            "missing_selected_articles": missing_articles,
            "duplicate_article_ids":     duplicate_article_ids,
            "empty_articles":            empty_articles,
        },
        "articles": all_articles,
    }

    strict_failures: list[str] = []
    if missing_documents:
        strict_failures.append(f"missing_documents={missing_documents}")
    if duplicate_article_ids:
        strict_failures.append(f"duplicate_article_ids={duplicate_article_ids}")
    if articles_created != articles_selected_total:
        strict_failures.append(
            f"selected_count_mismatch: "
            f"expected={articles_selected_total} created={articles_created}"
        )
    if empty_articles:
        strict_failures.append(f"empty_articles={empty_articles}")

    status_str = "PASS" if not strict_failures else "FAIL"

    try:
        _atomic_write(output_path, json.dumps(corpus, ensure_ascii=False, indent=2) + "\n")
        logger.info("Wrote %d articles to %s", articles_created, output_path)
    except Exception as exc:
        logger.error("Failed to write output: %s", exc)
        return 1

    report = {
        "documents_expected":        documents_expected,
        "documents_processed":       documents_processed,
        "articles_validated_total":  articles_validated_total,
        "articles_selected":         articles_created,
        "articles_excluded_by_scope": articles_excluded,
        "missing_expected_articles": missing_articles,
        "missing_selected_articles": missing_articles,
        "duplicate_article_ids":     duplicate_article_ids,
        "empty_articles":            empty_articles,
        "status":                    status_str,
        "adapter_version":           ADAPTER_VERSION,
        "warnings":                  all_warnings,
        "strict_failures":           strict_failures,
    }

    try:
        _atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        logger.info("Wrote report to %s", report_path)
    except Exception as exc:
        logger.error("Failed to write report: %s", exc)

    logger.info(
        "Done: docs=%d/%d validated=%d selected=%d excluded=%d failures=%d status=%s",
        documents_processed, documents_expected,
        articles_validated_total, articles_created, articles_excluded,
        len(strict_failures), status_str,
    )

    if args.strict and strict_failures:
        for f in strict_failures:
            logger.error("STRICT FAIL: %s", f)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
