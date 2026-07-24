#!/usr/bin/env python3
"""
Audit the 16 canonical VBPL API texts selected by the latest run manifest.

This audits API/gateway text quality, not PDF/OCR quality.

Usage:
    python scripts/audit_vbpl_corpus_quality.py

Exit codes:
    0: no FAIL results (PASS and WARN are allowed)
    1: at least one document failed a hard gate
    2: setup/input error
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import subprocess
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ARTICLE_RE = re.compile(
    r"(?im)^[ \t]*(?:điều|dieu)[ \t]+(\d{1,4})(?:[ \t]*[.:–—-])?[ \t]*"
)
HTML_TAG_RE = re.compile(r"<\/?[a-zA-Z][^>]{0,500}>")
SCRIPT_RE = re.compile(r"(?is)<script\b|javascript:|onclick\s*=")
WHITESPACE_RE = re.compile(r"\s+")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
# Detect only characteristic multi-character mojibake sequences.
# Single letters such as "Â" and "Ã" are valid Vietnamese characters and
# must never be treated as corruption by themselves.
MOJIBAKE_PATTERNS = (
    "Ã ", "Ã¡", "Ã¢", "Ã£", "Ã¨", "Ã©", "Ãª",
    "Ã¬", "Ã­", "Ã²", "Ã³", "Ã´", "Ãµ", "Ã¹",
    "Ãº", "Ã½", "Äƒ", "Ä‘", "Æ¡", "Æ°",
    "áº", "á»", "â€“", "â€”", "â€œ", "â€",
    "â€˜", "â€™", "ï¿½", "ðŸ",
)


@dataclass
class DocumentAudit:
    document_number: str
    snapshot_dir: str
    expected_articles: int | None = None
    item_id_expected: str | None = None
    verify_ok: bool = False
    manifest_ok: bool = False
    contract_present: bool = False
    full_text_present: bool = False
    char_count: int = 0
    word_count: int = 0
    article_heading_count: int = 0
    unique_article_count: int = 0
    duplicate_article_headings: list[int] = field(default_factory=list)
    missing_articles: list[int] = field(default_factory=list)
    unexpected_articles: list[int] = field(default_factory=list)
    first_seen_sequence_ok: bool = False
    min_article_chars: int | None = None
    p10_article_chars: float | None = None
    median_article_chars: float | None = None
    short_articles: list[int] = field(default_factory=list)
    replacement_char_count: int = 0
    control_char_count: int = 0
    zero_width_count: int = 0
    mojibake_count: int = 0
    html_tag_count: int = 0
    script_fragment_count: int = 0
    repeated_paragraph_ratio: float = 0.0
    document_number_found_in_text: bool = False
    gate_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "FAIL"


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = value.casefold()
    value = value.replace("–", "-").replace("—", "-")
    return WHITESPACE_RE.sub(" ", value).strip()


def compact_document_number(value: str) -> str:
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def percentile(values: list[int], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def first_seen(values: list[int]) -> list[int]:
    return list(dict.fromkeys(values))


def split_articles(text: str, expected_articles: int | None) -> dict[int, str]:
    """
    Use the first occurrence of each expected heading in the main sequence.
    Repeated headings after the main body are not used as canonical articles.
    """
    matches = list(ARTICLE_RE.finditer(text))
    articles: dict[int, str] = {}
    next_expected = 1

    for index, match in enumerate(matches):
        number = int(match.group(1))

        if expected_articles is not None:
            if number != next_expected:
                continue
            next_expected += 1
        elif number in articles:
            continue

        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        articles[number] = text[match.start():end]

        if expected_articles is not None and next_expected > expected_articles:
            break

    return articles


def repeated_paragraph_ratio(text: str) -> float:
    paragraphs = [
        normalize(part)
        for part in re.split(r"\n\s*\n+", text)
        if len(normalize(part)) >= 80
    ]
    if not paragraphs:
        return 0.0

    counts = Counter(paragraphs)
    repeated_chars = sum(
        len(paragraph) * (count - 1)
        for paragraph, count in counts.items()
        if count > 1
    )
    total_chars = sum(len(paragraph) for paragraph in paragraphs)
    return repeated_chars / total_chars if total_chars else 0.0


def load_config(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(doc["document_number"]): doc
        for doc in data.get("documents", [])
        if doc.get("source_adapter", "vbpl") == "vbpl"
    }


def run_verify(repo_root: Path, snapshot: Path, document_number: str) -> bool:
    process = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "vbpl_portal.py"),
            "verify",
            str(snapshot),
            "--document-number",
            document_number,
        ],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return process.returncode == 0


def manifest_document_number(manifest: dict[str, Any]) -> str | None:
    return (
        manifest.get("document", {}).get("document_number")
        or manifest.get("document_number")
    )


def manifest_item_id(manifest: dict[str, Any]) -> str | None:
    candidates = (
        manifest.get("document", {}).get("item_id"),
        manifest.get("item_id"),
        manifest.get("source", {}).get("item_id"),
        manifest.get("ingestion_contract", {}).get("item_id"),
    )
    for value in candidates:
        if value is not None:
            return str(value)
    return None


def audit_document(
    repo_root: Path,
    snapshot: Path,
    document_number: str,
    config_doc: dict[str, Any],
) -> DocumentAudit:
    expected_articles_raw = config_doc.get("expected_articles")
    expected_articles = (
        int(expected_articles_raw) if expected_articles_raw is not None else None
    )
    item_id_expected = (
        str(config_doc["item_id"]) if config_doc.get("item_id") is not None else None
    )

    result = DocumentAudit(
        document_number=document_number,
        snapshot_dir=str(snapshot),
        expected_articles=expected_articles,
        item_id_expected=item_id_expected,
    )

    manifest_path = snapshot / "manifest.json"
    text_path = snapshot / "full_text.txt"

    if not snapshot.is_dir():
        result.gate_failures.append("snapshot_missing")
        return result
    if not manifest_path.is_file():
        result.gate_failures.append("manifest_missing")
        return result

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result.manifest_ok = True
    except (OSError, json.JSONDecodeError):
        result.gate_failures.append("manifest_invalid")
        return result

    result.contract_present = bool(
        manifest.get("ingestion_contract_sha256")
        or manifest.get("ingestion_contract")
    )
    if not result.contract_present:
        result.gate_failures.append("ingestion_contract_missing")

    actual_number = manifest_document_number(manifest)
    if actual_number != document_number:
        result.gate_failures.append("manifest_document_number_mismatch")

    actual_item_id = manifest_item_id(manifest)
    if item_id_expected and actual_item_id and actual_item_id != item_id_expected:
        result.gate_failures.append("manifest_item_id_mismatch")

    gates = manifest.get("gates")
    if isinstance(gates, dict):
        false_gates = [name for name, value in gates.items() if value is False]
        if false_gates:
            result.gate_failures.append(
                "manifest_false_gates:" + ",".join(sorted(false_gates))
            )

    result.verify_ok = run_verify(repo_root, snapshot, document_number)
    if not result.verify_ok:
        result.gate_failures.append("snapshot_verify_failed")

    if not text_path.is_file():
        result.gate_failures.append("full_text_missing")
        return result

    result.full_text_present = True
    try:
        text = text_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        result.gate_failures.append("full_text_not_utf8")
        return result

    result.char_count = len(text)
    result.word_count = len(re.findall(r"\S+", text))
    if result.char_count < 500:
        result.gate_failures.append("full_text_too_short")

    numbers = [int(match.group(1)) for match in ARTICLE_RE.finditer(text)]
    unique = first_seen(numbers)
    result.article_heading_count = len(numbers)
    result.unique_article_count = len(unique)
    result.duplicate_article_headings = sorted(
        number for number, count in Counter(numbers).items() if count > 1
    )

    if expected_articles is not None:
        expected = list(range(1, expected_articles + 1))
        result.missing_articles = sorted(set(expected) - set(unique))
        result.unexpected_articles = sorted(set(unique) - set(expected))
        result.first_seen_sequence_ok = unique[:expected_articles] == expected

        if result.missing_articles:
            result.gate_failures.append("missing_expected_articles")
        if result.unexpected_articles:
            result.gate_failures.append("unexpected_article_numbers")
        if not result.first_seen_sequence_ok:
            result.gate_failures.append("article_first_seen_sequence_mismatch")
        if result.unique_article_count != expected_articles:
            result.gate_failures.append("unique_article_count_mismatch")
    else:
        result.first_seen_sequence_ok = unique == sorted(unique)

    articles = split_articles(text, expected_articles)
    article_lengths = {
        number: len(normalize(body))
        for number, body in articles.items()
    }
    lengths = list(article_lengths.values())
    if lengths:
        result.min_article_chars = min(lengths)
        result.p10_article_chars = round(percentile(lengths, 0.10) or 0.0, 3)
        result.median_article_chars = round(statistics.median(lengths), 3)
        result.short_articles = sorted(
            number for number, length in article_lengths.items() if length < 35
        )
        if result.short_articles:
            result.warnings.append("very_short_articles_need_review")
    else:
        result.gate_failures.append("no_articles_parsed")

    result.replacement_char_count = text.count("\ufffd")
    result.control_char_count = len(CONTROL_RE.findall(text))
    result.zero_width_count = len(ZERO_WIDTH_RE.findall(text))
    result.mojibake_count = sum(text.count(pattern) for pattern in MOJIBAKE_PATTERNS)
    result.html_tag_count = len(HTML_TAG_RE.findall(text))
    result.script_fragment_count = len(SCRIPT_RE.findall(text))
    result.repeated_paragraph_ratio = round(repeated_paragraph_ratio(text), 6)

    compact_text = compact_document_number(text[:10000])
    compact_number = compact_document_number(document_number)
    result.document_number_found_in_text = compact_number in compact_text

    if result.replacement_char_count:
        result.gate_failures.append("unicode_replacement_characters")
    if result.control_char_count:
        result.gate_failures.append("illegal_control_characters")
    if result.mojibake_count:
        result.gate_failures.append("mojibake_detected")
    if result.script_fragment_count:
        result.gate_failures.append("script_fragments_in_canonical_text")

    if result.html_tag_count:
        result.warnings.append("html_tags_remain")
    if result.zero_width_count:
        result.warnings.append("zero_width_characters")
    if result.repeated_paragraph_ratio > 0.03:
        result.warnings.append("high_repeated_paragraph_ratio")
    if result.duplicate_article_headings:
        result.warnings.append("repeated_article_headings")
    if not result.document_number_found_in_text:
        result.warnings.append("document_number_not_found_near_text_start")

    result.status = "FAIL" if result.gate_failures else (
        "WARN" if result.warnings else "PASS"
    )
    return result


def write_csv(path: Path, audits: list[DocumentAudit]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(item) for item in audits]
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    list_fields = {
        "duplicate_article_headings",
        "missing_articles",
        "unexpected_articles",
        "short_articles",
        "gate_failures",
        "warnings",
    }
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            for field_name in list_fields:
                row[field_name] = json.dumps(
                    row[field_name], ensure_ascii=False
                )
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/vbpl_corpus.json"),
    )
    parser.add_argument(
        "--run-manifest",
        type=Path,
        default=Path("data/raw/vbpl/run_manifest.json"),
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        default=Path("data/quality/vbpl_corpus_quality_report.json"),
    )
    parser.add_argument(
        "--csv-report",
        type=Path,
        default=Path("data/quality/vbpl_corpus_quality_report.csv"),
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    config_path = (
        args.config if args.config.is_absolute() else repo_root / args.config
    )
    run_manifest_path = (
        args.run_manifest
        if args.run_manifest.is_absolute()
        else repo_root / args.run_manifest
    )
    json_report = (
        args.json_report
        if args.json_report.is_absolute()
        else repo_root / args.json_report
    )
    csv_report = (
        args.csv_report
        if args.csv_report.is_absolute()
        else repo_root / args.csv_report
    )

    if not config_path.is_file() or not run_manifest_path.is_file():
        print("Missing config or run manifest.", file=sys.stderr)
        return 2

    config = load_config(config_path)
    run = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    results = run.get("results", [])

    audits: list[DocumentAudit] = []
    seen: set[str] = set()

    for item in results:
        document_number = str(item.get("document_number", ""))
        snapshot_value = item.get("snapshot_dir")
        if not document_number or not snapshot_value:
            continue
        if document_number not in config:
            continue

        snapshot = Path(snapshot_value)
        if not snapshot.is_absolute():
            snapshot = repo_root / snapshot

        audits.append(
            audit_document(
                repo_root,
                snapshot,
                document_number,
                config[document_number],
            )
        )
        seen.add(document_number)

    missing_from_run = sorted(set(config) - seen)

    summary = {
        "schema_version": "vbpl-corpus-quality-v1",
        "run_id": run.get("run_id"),
        "run_status": run.get("status"),
        "documents_expected": len(config),
        "documents_audited": len(audits),
        "passed": sum(item.status == "PASS" for item in audits),
        "warnings": sum(item.status == "WARN" for item in audits),
        "failed": sum(item.status == "FAIL" for item in audits),
        "missing_from_run": missing_from_run,
        "quality_policy": {
            "hard_gates": [
                "snapshot verify passes",
                "ingestion contract present",
                "UTF-8 full_text exists",
                "expected article set and first-seen sequence match",
                "no Unicode replacement/control/mojibake/script fragments",
            ],
            "soft_review": [
                "repeated article headings",
                "very short articles",
                "remaining HTML tags",
                "repeated paragraph ratio > 3%",
                "document number not found near text start",
            ],
        },
        "results": [asdict(item) for item in audits],
    }

    if missing_from_run:
        summary["failed"] += len(missing_from_run)

    json_report.parent.mkdir(parents=True, exist_ok=True)
    json_report.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(csv_report, audits)

    print(json.dumps(
        {
            "run_id": summary["run_id"],
            "documents_expected": summary["documents_expected"],
            "documents_audited": summary["documents_audited"],
            "passed": summary["passed"],
            "warnings": summary["warnings"],
            "failed": summary["failed"],
            "missing_from_run": summary["missing_from_run"],
            "json_report": str(json_report.relative_to(repo_root)),
            "csv_report": str(csv_report.relative_to(repo_root)),
        },
        ensure_ascii=False,
        indent=2,
    ))

    for item in audits:
        print(
            f"{item.status:4}  {item.document_number:24} "
            f"chars={item.char_count:<8} "
            f"articles={item.unique_article_count}/{item.expected_articles} "
            f"repeat={item.repeated_paragraph_ratio:.3%}"
        )
        if item.gate_failures:
            print("      FAIL:", ", ".join(item.gate_failures))
        if item.warnings:
            print("      WARN:", ", ".join(item.warnings))

    if missing_from_run:
        print("FAIL  Missing from run:", ", ".join(missing_from_run))

    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
