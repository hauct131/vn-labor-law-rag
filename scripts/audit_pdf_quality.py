#!/usr/bin/env python3
"""
Audit official PDF attachments against the gateway/HTML legal text stored in
each immutable VBPL snapshot.

Usage:
    python scripts/audit_pdf_quality.py \
        --root data/raw/vbpl \
        --json-report data/quality/pdf_quality_report.json \
        --csv-report data/quality/pdf_quality_report.csv

Exit codes:
    0: every audited PDF passed
    1: at least one PDF failed or needs OCR
    2: setup/input error
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


ARTICLE_RE = re.compile(
    r"(?im)^[ \t]*(?:điều|dieu)[ \t]+(\d{1,4})(?:[ \t]*[.:–—-])?[ \t]*"
)
PAGE_NUMBER_RE = re.compile(r"(?m)^\s*(?:trang\s*)?\d{1,4}\s*$", re.IGNORECASE)
SOFT_HYPHEN_RE = re.compile(r"\u00ad")
HYPHENATED_LINEBREAK_RE = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class PdfAudit:
    document_number: str
    snapshot_dir: str
    pdf_path: str
    pdf_sha256: str | None = None
    file_size_bytes: int | None = None
    pdf_signature_ok: bool = False
    qpdf_status: str = "not_available"
    qpdf_exit_code: int | None = None
    open_ok: bool = False
    encrypted: bool | None = None
    page_count: int = 0
    native_text_chars: int = 0
    median_text_chars_per_page: float = 0.0
    text_page_ratio: float = 0.0
    image_only_pages: int = 0
    extraction_class: str = "unknown"
    html_article_count: int = 0
    pdf_article_count: int = 0
    missing_articles: list[int] | None = None
    unexpected_articles: list[int] | None = None
    article_coverage: float | None = None
    median_article_similarity: float | None = None
    p10_article_similarity: float | None = None
    min_article_similarity: float | None = None
    full_text_similarity: float | None = None
    status: str = "FAIL"
    reasons: list[str] | None = None
    warnings: list[str] | None = None

    def __post_init__(self) -> None:
        if self.missing_articles is None:
            self.missing_articles = []
        if self.unexpected_articles is None:
            self.unexpected_articles = []
        if self.reasons is None:
            self.reasons = []
        if self.warnings is None:
            self.warnings = []


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = SOFT_HYPHEN_RE.sub("", value)
    value = HYPHENATED_LINEBREAK_RE.sub("", value)
    value = PAGE_NUMBER_RE.sub(" ", value)
    value = value.casefold()
    value = value.replace("–", "-").replace("—", "-")
    value = WHITESPACE_RE.sub(" ", value)
    return value.strip()


def first_seen(values: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(values))


def article_numbers(value: str) -> list[int]:
    return [int(match.group(1)) for match in ARTICLE_RE.finditer(value)]


def split_articles(value: str) -> dict[int, str]:
    matches = list(ARTICLE_RE.finditer(value))
    articles: dict[int, str] = {}

    for index, match in enumerate(matches):
        number = int(match.group(1))
        # Keep the first full occurrence. Repeated article references appended
        # later in amendment/reference sections must not overwrite the body.
        if number in articles:
            continue

        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        articles[number] = value[match.start():end]

    return articles


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def similarity(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    return SequenceMatcher(None, left_norm, right_norm, autojunk=False).ratio()


def locate_pdfs(snapshot: Path, manifest: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []

    for attachment in manifest.get("attachments", []):
        saved = attachment.get("saved_path")
        if saved:
            path = Path(saved)
            if not path.is_absolute():
                path = snapshot / path
            if path.is_file() and path.suffix.casefold() == ".pdf":
                candidates.append(path)

    for path in snapshot.glob("attachments/**/*.pdf"):
        if path.is_file():
            candidates.append(path)

    # Preserve order while deduplicating.
    return list(dict.fromkeys(path.resolve() for path in candidates))


def latest_contract_snapshots(root: Path) -> list[Path]:
    by_document: dict[str, tuple[str, Path]] = {}

    for manifest_path in root.glob("*/*/manifest.json"):
        if "_diagnostics" in manifest_path.parts or ".tmp-" in manifest_path.as_posix():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        document_number = (
            manifest.get("document", {}).get("document_number")
            or manifest.get("document_number")
        )
        if not document_number:
            continue

        # Prefer contract-aware snapshots, then newest retrieval timestamp/path.
        contract_rank = "1" if manifest.get("ingestion_contract_sha256") else "0"
        retrieved = str(manifest.get("retrieved_at", ""))
        rank = f"{contract_rank}:{retrieved}:{manifest_path.parent.name}"
        current = by_document.get(document_number)
        if current is None or rank > current[0]:
            by_document[document_number] = (rank, manifest_path.parent)

    return [item[1] for item in sorted(by_document.values(), key=lambda x: x[1].as_posix())]


def qpdf_check(pdf_path: Path) -> tuple[str, int | None, str]:
    executable = shutil.which("qpdf")
    if not executable:
        return "not_available", None, ""

    process = subprocess.run(
        [executable, "--check", str(pdf_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    output = (process.stdout + "\n" + process.stderr).strip()

    if process.returncode == 0:
        return "pass", 0, output
    if process.returncode == 3:
        return "warning", 3, output
    return "fail", process.returncode, output


def extract_pdf(pdf_path: Path) -> tuple[dict[str, Any], str]:
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PyMuPDF is missing. Install it with: python -m pip install pymupdf"
            ) from exc

    document = pymupdf.open(pdf_path)
    try:
        if document.needs_pass:
            return {
                "open_ok": True,
                "encrypted": True,
                "page_count": document.page_count,
                "page_text_lengths": [],
                "image_only_pages": 0,
            }, ""

        texts: list[str] = []
        page_text_lengths: list[int] = []
        image_only_pages = 0

        for page in document:
            text = page.get_text("text", sort=True) or ""
            texts.append(text)
            page_text_lengths.append(len(normalize_text(text)))

            image_count = len(page.get_images(full=True))
            if len(normalize_text(text)) < 20 and image_count > 0:
                image_only_pages += 1

        return {
            "open_ok": True,
            "encrypted": False,
            "page_count": document.page_count,
            "page_text_lengths": page_text_lengths,
            "image_only_pages": image_only_pages,
        }, "\n".join(texts)
    finally:
        document.close()


def audit_pdf(
    snapshot: Path,
    pdf_path: Path,
    manifest: dict[str, Any],
    *,
    min_article_similarity: float,
    min_p10_similarity: float,
) -> PdfAudit:
    document_number = (
        manifest.get("document", {}).get("document_number")
        or manifest.get("document_number")
        or snapshot.parent.name
    )
    audit = PdfAudit(
        document_number=document_number,
        snapshot_dir=str(snapshot),
        pdf_path=str(pdf_path),
    )

    try:
        audit.file_size_bytes = pdf_path.stat().st_size
        audit.pdf_sha256 = sha256_file(pdf_path)
        audit.pdf_signature_ok = pdf_path.read_bytes()[:5] == b"%PDF-"
    except OSError as exc:
        audit.reasons.append(f"file_read_error: {exc}")
        return audit

    if not audit.pdf_signature_ok:
        audit.reasons.append("missing_pdf_signature")

    qpdf_status, qpdf_exit_code, qpdf_output = qpdf_check(pdf_path)
    audit.qpdf_status = qpdf_status
    audit.qpdf_exit_code = qpdf_exit_code
    if qpdf_status == "fail":
        audit.reasons.append("qpdf_structural_check_failed")
    elif qpdf_status == "warning":
        audit.warnings.append("qpdf_reported_warnings")
    elif qpdf_status == "not_available":
        audit.warnings.append("qpdf_not_installed")

    try:
        metrics, pdf_text = extract_pdf(pdf_path)
    except Exception as exc:
        audit.reasons.append(f"pdf_open_or_extract_error: {exc}")
        return audit

    audit.open_ok = bool(metrics["open_ok"])
    audit.encrypted = bool(metrics["encrypted"])
    audit.page_count = int(metrics["page_count"])
    page_lengths = list(metrics["page_text_lengths"])
    audit.image_only_pages = int(metrics["image_only_pages"])
    audit.native_text_chars = sum(page_lengths)

    if page_lengths:
        audit.median_text_chars_per_page = round(statistics.median(page_lengths), 3)
        audit.text_page_ratio = round(
            sum(length >= 20 for length in page_lengths) / len(page_lengths), 6
        )

    if audit.encrypted:
        audit.extraction_class = "encrypted"
        audit.reasons.append("encrypted_pdf")
        return audit
    if audit.page_count <= 0:
        audit.reasons.append("zero_pages")
        return audit

    if audit.text_page_ratio >= 0.90 and audit.median_text_chars_per_page >= 100:
        audit.extraction_class = "born_digital"
    elif audit.text_page_ratio >= 0.40:
        audit.extraction_class = "mixed"
        audit.warnings.append("mixed_text_and_image_pages")
    else:
        audit.extraction_class = "scanned_or_broken_text_layer"
        audit.reasons.append("needs_ocr_or_text_layer_repair")

    html_path = snapshot / "full_text.txt"
    if not html_path.is_file():
        audit.warnings.append("full_text_reference_missing")
    else:
        html_text = html_path.read_text(encoding="utf-8")
        html_articles = split_articles(html_text)
        pdf_articles = split_articles(pdf_text)

        html_sequence = first_seen(article_numbers(html_text))
        pdf_sequence = first_seen(article_numbers(pdf_text))

        audit.html_article_count = len(html_sequence)
        audit.pdf_article_count = len(pdf_sequence)
        audit.missing_articles = sorted(set(html_sequence) - set(pdf_sequence))
        audit.unexpected_articles = sorted(set(pdf_sequence) - set(html_sequence))

        if html_sequence:
            common = set(html_sequence) & set(pdf_sequence)
            audit.article_coverage = round(len(common) / len(set(html_sequence)), 6)

            similarities = [
                similarity(html_articles[number], pdf_articles[number])
                for number in html_sequence
                if number in html_articles and number in pdf_articles
            ]
            if similarities:
                audit.median_article_similarity = round(
                    statistics.median(similarities), 6
                )
                audit.p10_article_similarity = round(
                    percentile(similarities, 0.10) or 0.0, 6
                )
                audit.min_article_similarity = round(min(similarities), 6)

        audit.full_text_similarity = round(similarity(html_text, pdf_text), 6)

        if audit.missing_articles:
            audit.reasons.append("missing_articles_in_pdf")
        if html_sequence and pdf_sequence[: len(html_sequence)] != html_sequence:
            audit.reasons.append("article_first_seen_sequence_mismatch")
        if (
            audit.median_article_similarity is not None
            and audit.median_article_similarity < min_article_similarity
        ):
            audit.reasons.append("median_article_similarity_below_threshold")
        if (
            audit.p10_article_similarity is not None
            and audit.p10_article_similarity < min_p10_similarity
        ):
            audit.reasons.append("p10_article_similarity_below_threshold")

        # Unexpected article headings may come from appendices or quoted legal
        # provisions. Keep them visible but do not fail automatically.
        if audit.unexpected_articles:
            audit.warnings.append("unexpected_article_headings_need_review")

    audit.status = "PASS" if not audit.reasons else "FAIL"
    return audit


def write_csv(path: Path, audits: list[PdfAudit]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(audit) for audit in audits]
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            row["missing_articles"] = json.dumps(row["missing_articles"], ensure_ascii=False)
            row["unexpected_articles"] = json.dumps(
                row["unexpected_articles"], ensure_ascii=False
            )
            row["reasons"] = json.dumps(row["reasons"], ensure_ascii=False)
            row["warnings"] = json.dumps(row["warnings"], ensure_ascii=False)
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/raw/vbpl"))
    parser.add_argument(
        "--json-report",
        type=Path,
        default=Path("data/quality/pdf_quality_report.json"),
    )
    parser.add_argument(
        "--csv-report",
        type=Path,
        default=Path("data/quality/pdf_quality_report.csv"),
    )
    parser.add_argument("--min-article-similarity", type=float, default=0.95)
    parser.add_argument("--min-p10-similarity", type=float, default=0.85)
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Root does not exist: {args.root}", file=sys.stderr)
        return 2

    snapshots = latest_contract_snapshots(args.root)
    audits: list[PdfAudit] = []
    snapshots_without_pdf: list[str] = []

    for snapshot in snapshots:
        manifest_path = snapshot / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pdfs = locate_pdfs(snapshot, manifest)

        if not pdfs:
            document_number = (
                manifest.get("document", {}).get("document_number")
                or snapshot.parent.name
            )
            snapshots_without_pdf.append(document_number)
            continue

        for pdf_path in pdfs:
            audits.append(
                audit_pdf(
                    snapshot,
                    pdf_path,
                    manifest,
                    min_article_similarity=args.min_article_similarity,
                    min_p10_similarity=args.min_p10_similarity,
                )
            )

    summary = {
        "schema_version": "pdf-quality-audit-v1",
        "root": str(args.root),
        "policy": {
            "min_median_article_similarity": args.min_article_similarity,
            "min_p10_article_similarity": args.min_p10_similarity,
            "article_coverage_required": 1.0,
            "qpdf_errors_allowed": False,
            "encrypted_allowed": False,
            "ocr_required_for_scanned_or_broken_text_layer": True,
        },
        "snapshots_scanned": len(snapshots),
        "pdfs_audited": len(audits),
        "passed": sum(a.status == "PASS" for a in audits),
        "failed": sum(a.status != "PASS" for a in audits),
        "snapshots_without_pdf": snapshots_without_pdf,
        "results": [asdict(audit) for audit in audits],
    }

    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(args.csv_report, audits)

    print(json.dumps(
        {
            "snapshots_scanned": summary["snapshots_scanned"],
            "pdfs_audited": summary["pdfs_audited"],
            "passed": summary["passed"],
            "failed": summary["failed"],
            "json_report": str(args.json_report),
            "csv_report": str(args.csv_report),
        },
        ensure_ascii=False,
        indent=2,
    ))

    for audit in audits:
        print(
            f"{audit.status:4}  {audit.document_number:24} "
            f"pages={audit.page_count:<4} "
            f"class={audit.extraction_class:<28} "
            f"coverage={audit.article_coverage} "
            f"median={audit.median_article_similarity}"
        )
        if audit.reasons:
            print("      reasons:", ", ".join(audit.reasons))
        if audit.warnings:
            print("      warnings:", ", ".join(audit.warnings))

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
