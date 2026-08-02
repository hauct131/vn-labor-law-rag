#!/usr/bin/env python3
"""Build a unified labour-law candidate release from official DOCX sources.

This builder intentionally keeps the uploaded DOCX bytes immutable.  It reads
OOXML parts in memory (including packages whose ZIP members use backslashes),
creates hash-bound raw snapshots, adds the two missing official documents to
the existing 285-unit VBPL corpus, chunks the unified corpus, and emits a
self-contained candidate release.

The output is a technical candidate.  It does not claim that a legal authority
has approved the effect review.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zipfile import ZipFile

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.ingestion.legal_chunker import (
    ChunkingConfig,
    build_legal_chunks,
    validate_chunks,
)
from scripts.audit_e5_token_lengths import load_audit_tokenizer
from scripts.build_vbpl_articles import parse_content_units


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
DS_NS = {"ds": "http://www.w3.org/2000/09/xmldsig#"}
RELEASE_SCHEMA = "labor-law-release-v1"
BUILDER_VERSION = "official-docx-release-v2"
LAW_AS_OF = "2026-07-27"
DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-large"
DEFAULT_MODEL_MAX_TOKENS = 512
DEFAULT_INDEXER_NEAR_LIMIT_TOKENS = 480
DEFAULT_OPERATIONAL_MAX_TOKENS = 479
DEFAULT_TARGET_TOKENS = 420
DEFAULT_TOKENIZER_THREADS = 6


DOCUMENTS: dict[str, dict[str, Any]] = {
    "18/VBHN-VPQH": {
        "slug": "18_vbhn_vpqh",
        "canonical_document_id": "vn:bll-2019",
        "title": "Bộ luật Lao động (văn bản hợp nhất số 18/VBHN-VPQH)",
        "source_file": "2026_131_18_VBHN-VPQH.docx",
        "official_page_url": (
            "https://vanban.chinhphu.vn/?docid=217002&pageid=27160"
        ),
        "source_item_id": "chinhphu:docid:217002",
        "expected_articles": 220,
        "selected_articles": list(range(1, 221)),
        "issued_at": "2026-02-12",
        "effective_from": "2021-01-01",
        "effective_to": None,
        "legal_status": "effective_consolidated_text",
        "source_type": "Văn bản hợp nhất",
    },
    "66.18/2026/NQ-CP": {
        "slug": "66_18_2026_nq_cp",
        "canonical_document_id": "vn:66.18-2026-nq-cp",
        "title": (
            "Nghị quyết 66.18/2026/NQ-CP về phân quyền, cắt giảm, "
            "đơn giản hóa thủ tục hành chính, điều kiện kinh doanh"
        ),
        "source_file": "2026_301_66.18_2026_NQ-CP.docx",
        "official_page_url": (
            "https://vanban.chinhphu.vn/?docid=218181&pageid=27160"
        ),
        "source_item_id": "chinhphu:docid:218181",
        "expected_articles": 7,
        "selected_articles": [4, 6],
        "issued_at": "2026-05-18",
        "effective_from": "2026-07-01",
        "effective_to": "2027-02-28",
        "legal_status": "effective_temporarily",
        "source_type": "Nghị quyết",
    },
}

NQ_APPENDIX_CODES = {
    "I": "NQ66.18.PL-I.4.C.I",
    "III": "NQ66.18.PL-I.4.C.III",
    "V": "NQ66.18.PL-I.4.C.V",
    "VII": "NQ66.18.PL-I.4.C.VII",
    "VIII": "NQ66.18.PL-I.4.C.VIII",
    "IX": "NQ66.18.PL-I.4.C.IX",
}

ARTICLE_CODE_PREFIXES = {
    "18/VBHN-VPQH": "20.2.LQ",
    "135/2020/NĐ-CP": "20.2.NĐ.2",
    "145/2020/NĐ-CP": "20.2.NĐ.3",
    "152/2020/NĐ-CP": "20.2.NĐ.4",
    "97/2022/NĐ-CP": "20.2.NĐ.6",
    "293/2025/NĐ-CP": "20.2.NĐ.8",
    "219/2025/NĐ-CP": "NĐ219",
    "128/2025/NĐ-CP": "NĐ128",
    "129/2025/NĐ-CP": "NĐ129",
    "09/2020/TT-BLĐTBXH": "20.2.TT.1",
    "10/2020/TT-BLĐTBXH": "20.2.TT.2",
    "04/2021/TT-BCT": "20.2.TT.3",
    "18/2021/TT-BLĐTBXH": "20.2.TT.4",
    "19/2021/TT-BLĐTBXH": "20.2.TT.5",
    "12/2022/TT-BCT": "20.2.TT.6",
    "20/2023/TT-BCT": "20.2.TT.7",
    "17/2023/TT-BLĐTBXH": "20.2.TT.8",
    "66.18/2026/NQ-CP": "NQ66.18",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\ufeff", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def normalized_member_map(archive: ZipFile) -> tuple[dict[str, str], bool]:
    mapping: dict[str, str] = {}
    used_backslashes = False
    for member in archive.namelist():
        normalized = member.replace("\\", "/")
        if normalized != member:
            used_backslashes = True
        if normalized in mapping:
            raise ValueError(
                f"Ambiguous OOXML member after path normalization: {normalized}"
            )
        mapping[normalized] = member
    return mapping, used_backslashes


def extract_docx_paragraphs(path: Path) -> tuple[list[str], dict[str, Any]]:
    """Return visible body paragraphs in document order and package metadata."""
    with ZipFile(path) as archive:
        members, used_backslashes = normalized_member_map(archive)
        document_member = members.get("word/document.xml")
        if not document_member:
            raise ValueError(f"{path.name}: word/document.xml is missing")

        root = etree.fromstring(archive.read(document_member))
        paragraphs: list[str] = []
        for paragraph in root.xpath(".//w:body//w:p", namespaces=NS):
            text = "".join(
                paragraph.xpath(".//w:t/text()", namespaces=NS)
            )
            text = normalize_text(text)
            if text:
                paragraphs.append(text)

        signature_members = sorted(
            normalized
            for normalized in members
            if normalized.startswith("_xmlsignatures/")
            and normalized.endswith(".xml")
        )
        signer_subjects: list[str] = []
        for normalized in signature_members:
            if normalized.endswith("origin.sigs"):
                continue
            try:
                sig_root = etree.fromstring(
                    archive.read(members[normalized])
                )
                for encoded in sig_root.xpath(
                    ".//ds:X509Certificate/text()", namespaces=DS_NS
                ):
                    subject = certificate_subject(encoded)
                    if subject and subject not in signer_subjects:
                        signer_subjects.append(subject)
            except Exception:
                continue

        metadata = {
            "zip_member_count": len(archive.namelist()),
            "nonconformant_backslash_member_names": used_backslashes,
            "in_memory_member_path_normalization": used_backslashes,
            "digital_signature_parts_present": bool(signature_members),
            "digital_signature_part_names": signature_members,
            "signer_certificate_subjects": signer_subjects,
            "cryptographic_signature_validation": "not_performed",
        }
    return paragraphs, metadata


def certificate_subject(encoded: str) -> str | None:
    try:
        from cryptography import x509

        certificate = x509.load_der_x509_certificate(
            base64.b64decode(re.sub(r"\s+", "", encoded))
        )
        return certificate.subject.rfc4514_string()
    except Exception:
        return None


ARTICLE_RE = re.compile(r"^Điều\s+(\d+)\s*[.．]\s*(.*)$", re.IGNORECASE)
INTER_ARTICLE_HEADING_RE = re.compile(
    r"^(?:Chương|Mục)\s+(?:[IVXLCDM]+|\d+[A-Za-zĐđ]?)\s*[.．]?$",
    re.IGNORECASE,
)


class E5PassageTokenCounter:
    """Exact pre-truncation counter for E5 document embeddings."""

    def __init__(self, tokenizer: Any, model_name: str) -> None:
        if tokenizer is None:
            raise TypeError("tokenizer cannot be None")
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be a non-empty string")
        self._tokenizer = tokenizer
        self._prefix = "passage: " if "e5" in model_name.casefold() else ""
        self.name = f"fastembed-tokenizers:{model_name}:document"

    def count(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("Input text must be a string")
        encoding = self._tokenizer.encode(
            f"{self._prefix}{text}",
            add_special_tokens=True,
        )
        return len(encoding.ids)


def strip_inter_article_headings(body_lines: list[str]) -> list[str]:
    """Remove chapter/section headings that introduce the next article."""

    for index, line in enumerate(body_lines):
        if INTER_ARTICLE_HEADING_RE.fullmatch(normalize_text(line)):
            return body_lines[:index]
    return body_lines


def find_inter_article_heading_leaks(
    articles: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Locate standalone chapter/section headings inside article units."""

    leaks: list[dict[str, str]] = []
    for article in articles:
        for unit in article.get("content_units", []):
            for line in str(unit.get("text", "")).splitlines():
                normalized = normalize_text(line)
                if INTER_ARTICLE_HEADING_RE.fullmatch(normalized):
                    leaks.append(
                        {
                            "article_code": str(article.get("article_code")),
                            "unit_id": str(unit.get("unit_id")),
                            "heading": normalized,
                        }
                    )
    return leaks


def locate_main_article_sequence(
    paragraphs: list[str],
    expected_articles: int,
) -> list[tuple[int, int, str]]:
    headings: list[tuple[int, int, str]] = []
    expected = 1
    for index, text in enumerate(paragraphs):
        match = ARTICLE_RE.match(text)
        if not match:
            continue
        number = int(match.group(1))
        if number == expected:
            headings.append((number, index, normalize_text(match.group(2))))
            expected += 1
            if expected > expected_articles:
                break
        elif headings and number != expected:
            # A valid main sequence must be contiguous.  Do not silently skip
            # an unexpected article once sequence detection has started.
            if number <= expected_articles:
                raise ValueError(
                    "Non-contiguous main article sequence: "
                    f"expected Điều {expected}, found Điều {number} "
                    f"at paragraph {index + 1}"
                )
    found = [number for number, _, _ in headings]
    wanted = list(range(1, expected_articles + 1))
    if found != wanted:
        raise ValueError(
            f"Expected main articles 1-{expected_articles}, found {found}"
        )
    return headings


def administrative_tail_index(
    paragraphs: list[str], start: int, default: int
) -> int:
    patterns = (
        re.compile(r"^VĂN PHÒNG QUỐC HỘI$"),
        re.compile(r"^TM\. CHÍNH PHỦ$"),
        re.compile(r"^Phụ lục I$", re.IGNORECASE),
    )
    for index in range(start, len(paragraphs)):
        if any(pattern.match(paragraphs[index]) for pattern in patterns):
            return index
    return default


def build_main_articles(
    paragraphs: list[str],
    meta: dict[str, Any],
    source_sha256: str,
    package_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    headings = locate_main_article_sequence(
        paragraphs, meta["expected_articles"]
    )
    selected = set(meta["selected_articles"])
    result: list[dict[str, Any]] = []

    for position, (number, index, title) in enumerate(headings):
        if number not in selected:
            continue
        if position + 1 < len(headings):
            end = headings[position + 1][1]
        else:
            end = administrative_tail_index(
                paragraphs, index + 1, len(paragraphs)
            )
        body_lines = strip_inter_article_headings(
            paragraphs[index + 1 : end]
        )
        body_text = "\n".join(body_lines).strip()
        if not body_text:
            raise ValueError(
                f"{meta['source_file']}: Điều {number} has an empty body"
            )
        canonical_id = meta["canonical_document_id"]
        result.append(
            {
                "container_type": "article",
                "article_id": f"{canonical_id}:article:{number}",
                "article_code": canonical_article_code(
                    document_number_for(meta), number
                ),
                "article_number": number,
                "article_title": title,
                "heading": f"Điều {number}. {title}",
                "document_id": canonical_id,
                "source_document_id": (
                    f"official-docx:sha256:{source_sha256}"
                ),
                "document_number": document_number_for(meta),
                "source_item_id": meta["source_item_id"],
                "source_adapter": "official_government_docx",
                "corpus_role": "canonical",
                "source_urls": [meta["official_page_url"]],
                "source_sha256": source_sha256,
                "retrieved_at": generated_at(),
                "issued_at": meta["issued_at"],
                "effective_from": meta["effective_from"],
                "effective_to": meta["effective_to"],
                "legal_status": meta["legal_status"],
                "law_as_of": LAW_AS_OF,
                "issuing_authority": (
                    "Văn phòng Quốc hội"
                    if document_number_for(meta) == "18/VBHN-VPQH"
                    else "Chính phủ"
                ),
                "document_title": meta["title"],
                "topic_code": None,
                "topic_name": None,
                "codification_code": None,
                "chapter": None,
                "section": None,
                "source_type": meta["source_type"],
                "source_note_text": (
                    "Trích trực tiếp từ DOCX chính thức; file gốc được giữ "
                    "nguyên và khóa bằng SHA-256."
                ),
                "parser_version": BUILDER_VERSION,
                "relations": [],
                "attachments": [],
                "tables": [],
                "content_units": parse_content_units(
                    body_text,
                    doc_id=canonical_id,
                    article_num=number,
                ),
                "source_package": package_meta,
            }
        )
    return result


def document_number_for(meta: dict[str, Any]) -> str:
    for number, candidate in DOCUMENTS.items():
        if candidate is meta:
            return number
    raise KeyError("Document metadata is not registered")


def canonical_article_code(document_number: str, article_number: int) -> str:
    prefix = ARTICLE_CODE_PREFIXES.get(document_number)
    if not prefix:
        raise KeyError(
            f"No canonical article-code prefix for {document_number}"
        )
    return f"{prefix}.{article_number}"


def find_nq_appendix_i4_bounds(paragraphs: list[str]) -> tuple[int, int]:
    starts: list[int] = []
    for index, text in enumerate(paragraphs):
        if text.casefold() != "phụ lục i.4".casefold():
            continue
        lookahead_items = paragraphs[index + 1 : index + 5]
        lookahead = " ".join(lookahead_items).upper()
        has_body_start = any(
            item.casefold() == "mục 1".casefold()
            for item in lookahead_items
        )
        if (
            "PHẠM VI QUẢN LÝ CỦA BỘ NỘI VỤ" in lookahead
            and has_body_start
        ):
            starts.append(index)
    if len(starts) != 1:
        raise ValueError(
            f"Expected exactly one substantive Phụ lục I.4, found {starts}"
        )
    start = starts[0]
    for index in range(start + 1, len(paragraphs)):
        if paragraphs[index].casefold() == "phụ lục i.5".casefold():
            return start, index
    raise ValueError("Could not find end boundary Phụ lục I.5")


def build_nq_appendix_units(
    paragraphs: list[str],
    meta: dict[str, Any],
    source_sha256: str,
    package_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    start, end = find_nq_appendix_i4_bounds(paragraphs)
    appendix = paragraphs[start:end]

    c_heading = next(
        (
            i
            for i, text in enumerate(appendix)
            if text.upper() == "C. LĨNH VỰC LAO ĐỘNG, TIỀN LƯƠNG"
        ),
        None,
    )
    if c_heading is None:
        raise ValueError("Missing Phụ lục I.4 Mục 1 C labour heading")

    # The first D heading closes Mục 1/C.  This prevents the separate Mục 2
    # labour heading from being mixed into the selected alias units.
    c_end = next(
        (
            i
            for i in range(c_heading + 1, len(appendix))
            if re.match(r"^D\.\s+", appendix[i])
        ),
        None,
    )
    if c_end is None:
        raise ValueError("Missing boundary after Phụ lục I.4 Mục 1 C")

    roman_re = re.compile(r"^([IVXLCDM]+)\.\s+(.*)$")
    headings: list[tuple[str, int, str]] = []
    for index in range(c_heading + 1, c_end):
        match = roman_re.match(appendix[index])
        if match:
            headings.append(
                (match.group(1), index, normalize_text(match.group(2)))
            )

    roman_labels = [label for label, _, _ in headings]
    if roman_labels[:9] != [
        "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"
    ]:
        raise ValueError(
            "Unexpected Phụ lục I.4/C structure: "
            f"{roman_labels[:12]}"
        )

    heading_by_label = {label: (index, title) for label, index, title in headings}
    result: list[dict[str, Any]] = []
    canonical_id = meta["canonical_document_id"]

    for label, alias in NQ_APPENDIX_CODES.items():
        index, title = heading_by_label[label]
        next_indexes = [
            other_index
            for _, other_index, _ in headings
            if other_index > index
        ]
        unit_end = min(next_indexes) if next_indexes else c_end
        raw_lines = appendix[index:unit_end]
        body_text = "\n".join(raw_lines).strip()
        if not body_text:
            raise ValueError(f"Empty appendix unit {alias}")

        # Synthetic article containers are deliberate: the existing chunker
        # already treats article_id as the stable parent key, while article_code
        # is allowed to be a legal evidence code rather than a numeric value.
        result.append(
            {
                "container_type": "article",
                "article_id": (
                    f"{canonical_id}:appendix:I.4:C:{label}"
                ),
                "article_code": alias,
                "article_number": None,
                "article_title": (
                    f"Phụ lục I.4, Mục 1, C.{label} — {title}"
                ),
                "heading": (
                    f"Phụ lục I.4, Mục 1, C.{label}. {title}"
                ),
                "document_id": canonical_id,
                "source_document_id": (
                    f"official-docx:sha256:{source_sha256}"
                ),
                "document_number": "66.18/2026/NQ-CP",
                "source_item_id": meta["source_item_id"],
                "source_adapter": "official_government_docx",
                "corpus_role": "canonical_appendix_evidence",
                "source_urls": [meta["official_page_url"]],
                "source_sha256": source_sha256,
                "retrieved_at": generated_at(),
                "issued_at": meta["issued_at"],
                "effective_from": meta["effective_from"],
                "effective_to": meta["effective_to"],
                "legal_status": meta["legal_status"],
                "law_as_of": LAW_AS_OF,
                "issuing_authority": "Chính phủ",
                "document_title": meta["title"],
                "topic_code": "labor",
                "topic_name": "Lao động, tiền lương",
                "codification_code": alias,
                "chapter": None,
                "section": {
                    "number": f"I.4/C/{label}",
                    "title": "Lĩnh vực lao động, tiền lương",
                    "anchor_id": (
                        f"{canonical_id}:appendix:I.4:C:{label}"
                    ),
                },
                "source_type": "Phụ lục Nghị quyết",
                "source_note_text": (
                    "Đơn vị bằng chứng tách theo cây tiêu đề Phụ lục I.4, "
                    "Mục 1, C; không phải một Điều độc lập."
                ),
                "parser_version": BUILDER_VERSION,
                "relations": [],
                "attachments": [],
                "tables": [],
                "content_units": appendix_content_units(
                    body_text, canonical_id, label
                ),
                "source_package": package_meta,
            }
        )
    return result


def appendix_content_units(
    body_text: str, canonical_id: str, roman_label: str
) -> list[dict[str, Any]]:
    lines = [line for line in body_text.splitlines() if line.strip()]
    units: list[dict[str, Any]] = []
    active_clause: str | None = None
    occurrence: dict[tuple[str, str], int] = {}

    for position, text in enumerate(lines, start=1):
        clause_match = re.match(r"^(\d+)\.\s+", text)
        point_match = re.match(r"^([a-zđ])\)\s+", text)
        if clause_match:
            active_clause = clause_match.group(1)
            units.append(
                {
                    "unit_id": (
                        f"{canonical_id}:appendix:I.4:C:{roman_label}"
                        f"|clause={active_clause}"
                    ),
                    "unit_type": "clause",
                    "clause_number": active_clause,
                    "text": text,
                }
            )
        elif point_match and active_clause:
            label = point_match.group(1)
            key = (active_clause, label)
            occurrence[key] = occurrence.get(key, 0) + 1
            units.append(
                {
                    "unit_id": (
                        f"{canonical_id}:appendix:I.4:C:{roman_label}"
                        f"|clause={active_clause}|point={label}"
                        f"|occurrence={occurrence[key]}"
                    ),
                    "unit_type": "point",
                    "clause_number": active_clause,
                    "point_label": label,
                    "unit_occurrence": occurrence[key],
                    "text": text,
                }
            )
        else:
            units.append(
                {
                    "unit_id": (
                        f"{canonical_id}:appendix:I.4:C:{roman_label}"
                        f"|preamble={position}"
                    ),
                    "unit_type": "preamble",
                    "text": text,
                }
            )
    return units


def generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def create_raw_snapshot(
    raw_root: Path,
    source_path: Path,
    document_number: str,
    meta: dict[str, Any],
    paragraphs: list[str],
    package_meta: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    source_sha = sha256_file(source_path)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = raw_root / meta["slug"] / f"{timestamp}-{source_sha[:8]}"
    snapshot.mkdir(parents=True, exist_ok=False)

    original = snapshot / "original.docx"
    shutil.copy2(source_path, original)
    full_text = snapshot / "full_text.txt"
    atomic_write_text(full_text, "\n".join(paragraphs) + "\n")

    manifest = {
        "schema_version": "official-docx-source-snapshot-v1",
        "snapshot_status": "technical_verified_pending_legal_review",
        "review_status": "pending_authority_review",
        "retrieved_at": generated_at(),
        "law_as_of": LAW_AS_OF,
        "document": {
            "document_number": document_number,
            "canonical_document_id": meta["canonical_document_id"],
            "title": meta["title"],
            "issued_at": meta["issued_at"],
            "effective_from": meta["effective_from"],
            "effective_to": meta["effective_to"],
            "legal_status": meta["legal_status"],
        },
        "source": {
            "provider": "Cổng Thông tin điện tử Chính phủ",
            "official_page_url": meta["official_page_url"],
            "source_item_id": meta["source_item_id"],
            "source_format": "docx",
            "uploaded_filename": source_path.name,
        },
        "content_hashes": {
            "original_docx_sha256": source_sha,
            "full_text_sha256": sha256_file(full_text),
        },
        "package": package_meta,
        "approval": {
            "required": True,
            "status": "pending_authority_review",
            "hash_binding": source_sha,
        },
    }
    write_json(snapshot / "manifest.json", manifest)
    checksums = checksum_lines(
        snapshot, ["original.docx", "full_text.txt", "manifest.json"]
    )
    atomic_write_text(snapshot / "SHA256SUMS.txt", checksums)
    return snapshot, manifest


def checksum_lines(root: Path, relative_paths: Iterable[str]) -> str:
    return "".join(
        f"{sha256_file(root / relative)}  {relative}\n"
        for relative in sorted(relative_paths)
    )


def load_base_corpus(path: Path) -> dict[str, Any]:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    articles = corpus.get("articles", [])
    if len(articles) != 285:
        raise ValueError(
            f"Base VBPL corpus must contain 285 articles, found {len(articles)}"
        )
    excluded_docs = {"vn:bll-2019", "vn:66.18-2026-nq-cp"}
    if any(article.get("document_id") in excluded_docs for article in articles):
        raise ValueError("Base corpus unexpectedly already contains DOCX overlays")
    return corpus


def normalize_base_article_codes(
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for source_article in articles:
        article = dict(source_article)
        document_number = article.get("document_number")
        article_number = article.get("article_number")
        if not isinstance(document_number, str):
            raise ValueError(
                f"Base article missing document_number: "
                f"{article.get('article_id')}"
            )
        if not isinstance(article_number, int):
            raise ValueError(
                f"Base article missing numeric article_number: "
                f"{article.get('article_id')}"
            )
        article["source_article_code"] = article.get("article_code")
        article["article_code"] = canonical_article_code(
            document_number, article_number
        )
        normalized.append(article)
    return normalized


def audit_base_vbpl_snapshots(run_manifest_path: Path) -> dict[str, Any]:
    run_manifest = json.loads(
        run_manifest_path.read_text(encoding="utf-8")
    )
    records: list[dict[str, Any]] = []
    for result in run_manifest.get("results", []):
        snapshot = Path(result["snapshot_dir"])
        manifest_path = snapshot / "manifest.json"
        full_text_path = snapshot / "full_text.txt"
        missing = [
            relative
            for relative in (
                "SHA256SUMS.txt",
                "portal/api_responses.json",
            )
            if not (snapshot / relative).is_file()
        ]
        expected_hash = None
        actual_hash = None
        review_status = None
        if manifest_path.is_file():
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            expected_hash = manifest.get("content_hashes", {}).get(
                "full_text_text_sha256"
            )
            review_status = manifest.get("review_status")
        if full_text_path.is_file():
            actual_hash = sha256_file(full_text_path)
        records.append(
            {
                "document_number": result["document_number"],
                "snapshot_dir": portable_path(snapshot),
                "full_text_hash_matches_manifest": (
                    bool(expected_hash)
                    and actual_hash == expected_hash
                ),
                "missing_required_artifacts": missing,
                "review_status": review_status,
                "fully_verifiable": (
                    not missing
                    and bool(expected_hash)
                    and actual_hash == expected_hash
                    and review_status not in {
                        None,
                        "staged_unapproved",
                    }
                ),
            }
        )
    return {
        "run_manifest": portable_path(run_manifest_path),
        "document_count": len(records),
        "full_text_hash_match_count": sum(
            bool(record["full_text_hash_matches_manifest"])
            for record in records
        ),
        "fully_verifiable_count": sum(
            bool(record["fully_verifiable"]) for record in records
        ),
        "status": (
            "complete"
            if records and all(
                record["fully_verifiable"] for record in records
            )
            else "incomplete_supplied_archive"
        ),
        "records": records,
    }


def build_release(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = args.source_dir.resolve()
    raw_root = args.raw_root.resolve()
    requested_release_dir = args.release_dir.resolve()
    release_id = requested_release_dir.name
    base_path = args.base_corpus.resolve()

    if requested_release_dir.exists():
        raise FileExistsError(
            f"Release directory already exists: {requested_release_dir}"
        )
    requested_release_dir.parent.mkdir(parents=True, exist_ok=True)
    release_dir = Path(
        tempfile.mkdtemp(
            dir=str(requested_release_dir.parent),
            prefix=f".{release_id}.staging.",
        )
    )

    all_new_articles: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    raw_snapshots: list[dict[str, Any]] = []

    for document_number, meta in DOCUMENTS.items():
        source_path = source_dir / meta["source_file"]
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        paragraphs, package_meta = extract_docx_paragraphs(source_path)
        source_sha = sha256_file(source_path)

        snapshot, snapshot_manifest = create_raw_snapshot(
            raw_root,
            source_path,
            document_number,
            meta,
            paragraphs,
            package_meta,
        )
        raw_snapshots.append(
            {
                "document_number": document_number,
                "snapshot_dir": portable_path(snapshot),
                "manifest_sha256": sha256_file(snapshot / "manifest.json"),
            }
        )

        new_articles = build_main_articles(
            paragraphs, meta, source_sha, package_meta
        )
        if document_number == "66.18/2026/NQ-CP":
            new_articles.extend(
                build_nq_appendix_units(
                    paragraphs, meta, source_sha, package_meta
                )
            )
        all_new_articles.extend(new_articles)

        destination = (
            release_dir / "sources" / "official_docx" / source_path.name
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        source_records.append(
            {
                "document_number": document_number,
                "canonical_document_id": meta["canonical_document_id"],
                "provider": "Cổng Thông tin điện tử Chính phủ",
                "official_page_url": meta["official_page_url"],
                "source_item_id": meta["source_item_id"],
                "release_path": str(destination.relative_to(release_dir)),
                "sha256": source_sha,
                "size_bytes": source_path.stat().st_size,
                "package": package_meta,
                "raw_snapshot": portable_path(snapshot),
                "raw_snapshot_manifest_sha256": sha256_file(
                    snapshot / "manifest.json"
                ),
            }
        )

    if len(all_new_articles) != 228:
        raise ValueError(
            f"Expected 228 DOCX-derived containers, found {len(all_new_articles)}"
        )
    if len({a["article_id"] for a in all_new_articles}) != 228:
        raise ValueError("Duplicate DOCX-derived article IDs")
    heading_leaks = find_inter_article_heading_leaks(all_new_articles)
    if heading_leaks:
        raise ValueError(
            "Inter-article chapter/section headings leaked into article "
            f"content: {heading_leaks[:10]}"
        )

    base = load_base_corpus(base_path)
    base_snapshot_audit = audit_base_vbpl_snapshots(
        Path("data/raw/vbpl/run_manifest.json")
    )
    unified_articles = (
        normalize_base_article_codes(base["articles"]) + all_new_articles
    )
    if len(unified_articles) != 513:
        raise ValueError(
            f"Expected unified 513 containers, found {len(unified_articles)}"
        )
    if len({a["article_id"] for a in unified_articles}) != 513:
        raise ValueError("Duplicate unified article IDs")
    article_codes = [a["article_code"] for a in unified_articles]
    if len(set(article_codes)) != 513:
        duplicates = sorted(
            code
            for code in set(article_codes)
            if article_codes.count(code) > 1
        )
        raise ValueError(f"Duplicate canonical article codes: {duplicates}")

    unified_corpus = {
        "metadata": {
            "schema_version": "unified-labor-corpus-v1",
            "builder_version": BUILDER_VERSION,
            "release_id": release_id,
            "release_status": "technical_candidate_pending_authority_review",
            "law_as_of": LAW_AS_OF,
            "generated_at": generated_at(),
            "base_vbpl_article_count": 285,
            "official_docx_container_count": 228,
            "article_container_count": 513,
            "document_count": 18,
            "approval_required": True,
            "approval_status": "pending_authority_review",
        },
        "articles": unified_articles,
        "attachments": base.get("attachments", []),
    }
    write_json(release_dir / "articles.json", unified_corpus)

    tokenizer_adapter = load_audit_tokenizer(
        model_name=args.embedding_model,
        threads=args.tokenizer_threads,
        local_files_only=args.local_files_only,
        cache_dir=args.cache_dir,
        expected_max_tokens=args.expected_model_max_tokens,
    )
    token_counter = E5PassageTokenCounter(
        tokenizer_adapter.audit_tokenizer,
        args.embedding_model,
    )
    if not (
        0
        < args.target_tokens
        <= args.operational_max_tokens
        < DEFAULT_INDEXER_NEAR_LIMIT_TOKENS
        <= tokenizer_adapter.model_max_length
    ):
        raise ValueError(
            "Invalid token limits: require 0 < target_tokens <= "
            "operational_max_tokens < 480 <= model_max_tokens"
        )
    chunk_config = ChunkingConfig(
        target_tokens=args.target_tokens,
        max_tokens=args.operational_max_tokens,
    )
    chunks = build_legal_chunks(
        unified_corpus,
        config=chunk_config,
        token_counter=token_counter,
    )
    validation = validate_chunks(
        unified_corpus,
        chunks,
        config=chunk_config,
        token_counter=token_counter,
    )
    if not validation.get("is_valid"):
        raise ValueError(
            f"Unified chunk validation failed: {validation.get('errors', [])[:10]}"
        )

    atomic_write_text(
        release_dir / "chunks.jsonl",
        "".join(
            json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks
        ),
    )

    source_inventory = {
        "schema_version": "source-inventory-v1",
        "release_id": release_id,
        "generated_at": generated_at(),
        "law_as_of": LAW_AS_OF,
        "base_corpus": {
            "path": portable_path(base_path),
            "sha256": sha256_file(base_path),
            "article_count": 285,
            "source_scope": "16 VBPL portal documents",
            "snapshot_audit": base_snapshot_audit,
        },
        "official_docx_sources": source_records,
        "raw_snapshots": raw_snapshots,
        "provenance_statement": (
            "The original DOCX bytes are preserved unchanged and hash-bound. "
            "OOXML member-name normalization is applied only in memory for "
            "parsing the nonconformant NQ package."
        ),
    }
    write_json(release_dir / "source_inventory.json", source_inventory)

    effect_review = {
        "schema_version": "legal-effect-review-v1",
        "release_id": release_id,
        "law_as_of": LAW_AS_OF,
        "review_type": "technical_source-clause_review",
        "authority_review_status": "pending",
        "documents": [
            {
                "document_number": "18/VBHN-VPQH",
                "included_scope": "Điều 1-220",
                "status_at_law_as_of": "effective_consolidated_text",
                "basis": (
                    "The consolidation identifies amendments 71/2025/QH15, "
                    "113/2025/QH15 and 124/2025/QH15; all relevant effective "
                    "dates are on or before 2026-07-27."
                ),
            },
            {
                "document_number": "66.18/2026/NQ-CP",
                "included_scope": (
                    "Điều 4, Điều 6; Phụ lục I.4/Mục 1/C."
                    "I,III,V,VII,VIII,IX"
                ),
                "status_at_law_as_of": "effective_temporarily",
                "effective_from": "2026-07-01",
                "effective_to": "2027-02-28",
                "basis": "Khoản 1 Điều 7 của Nghị quyết.",
                "temporal_warning": (
                    "Khoản 4 Điều 7 can terminate corresponding provisions "
                    "earlier when later instruments enter into force."
                ),
            },
        ],
        "approval": {
            "required": True,
            "status": "pending_authority_review",
            "not_legal_advice": True,
        },
    }
    write_json(release_dir / "legal_effect_review.json", effect_review)

    approval = {
        "schema_version": "release-approval-v1",
        "release_id": release_id,
        "required": True,
        "status": "pending_authority_review",
        "instructions": (
            "An authorized reviewer must verify legal_effect_review.json and "
            "bind approval to manifest_sha256 before production publication."
        ),
        "approved_by": None,
        "approved_at": None,
        "manifest_sha256": None,
    }
    write_json(release_dir / "approval.json", approval)

    manifest = {
        "schema_version": RELEASE_SCHEMA,
        "release_id": release_id,
        "release_status": "technical_candidate_pending_authority_review",
        "law_as_of": LAW_AS_OF,
        "generated_at": generated_at(),
        "builder_version": BUILDER_VERSION,
        "counts": {
            "documents": 18,
            "base_vbpl_articles": 285,
            "docx_main_articles": 222,
            "docx_appendix_evidence_units": 6,
            "article_containers": 513,
            "chunks": len(chunks),
        },
        "hashes": {
            "articles_sha256": sha256_file(release_dir / "articles.json"),
            "chunks_sha256": sha256_file(release_dir / "chunks.jsonl"),
            "source_inventory_sha256": sha256_file(
                release_dir / "source_inventory.json"
            ),
            "legal_effect_review_sha256": sha256_file(
                release_dir / "legal_effect_review.json"
            ),
        },
        "chunking": {
            "target_tokens": chunk_config.target_tokens,
            "max_tokens": chunk_config.max_tokens,
            "operational_max_tokens": chunk_config.max_tokens,
            "indexer_near_limit_tokens": (
                DEFAULT_INDEXER_NEAR_LIMIT_TOKENS
            ),
            "model_max_tokens": tokenizer_adapter.model_max_length,
            "tokenizer": token_counter.name,
            "embedding_model": args.embedding_model,
            "exact_pre_truncation_count": True,
            "validation": validation,
        },
        "gates": {
            "source_hashes_verified": False,
            "official_docx_source_hashes_verified": True,
            "base_vbpl_full_text_hashes_match": (
                base_snapshot_audit["full_text_hash_match_count"] == 16
            ),
            "base_vbpl_snapshot_verification_passed": (
                base_snapshot_audit["fully_verifiable_count"] == 16
            ),
            "base_vbpl_snapshot_status": base_snapshot_audit["status"],
            "main_article_sequences_verified": True,
            "appendix_boundaries_verified": True,
            "chunk_validation_passed": True,
            "e5_token_limit_verified": True,
            "e5_token_limit_status": (
                "verified_during_build_below_operational_cap"
            ),
            "authority_review_passed": False,
            "production_publishable": False,
        },
    }
    write_json(release_dir / "manifest.json", manifest)

    approval["manifest_sha256"] = sha256_file(release_dir / "manifest.json")
    write_json(release_dir / "approval.json", approval)

    release_files = [
        str(path.relative_to(release_dir))
        for path in release_dir.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    ]
    atomic_write_text(
        release_dir / "SHA256SUMS.txt",
        checksum_lines(release_dir, release_files),
    )

    report = {
        "status": "PASS_TECHNICAL_CANDIDATE",
        "release_dir": portable_path(Path("data/releases") / release_id),
        "release_id": release_id,
        "article_containers": 513,
        "chunks": len(chunks),
        "official_docx_sources": 2,
        "raw_snapshots": 2,
        "authority_review_status": "pending",
        "production_publishable": False,
        "manifest_sha256": sha256_file(release_dir / "manifest.json"),
    }
    write_json(release_dir / "BUILD_REPORT.json", report)

    # BUILD_REPORT was added after the first inventory; regenerate inventory so
    # every deliverable file except SHA256SUMS itself is covered.
    release_files = [
        str(path.relative_to(release_dir))
        for path in release_dir.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    ]
    atomic_write_text(
        release_dir / "SHA256SUMS.txt",
        checksum_lines(release_dir, release_files),
    )
    checksum_inventory_sha256 = sha256_file(
        release_dir / "SHA256SUMS.txt"
    )
    os.replace(release_dir, requested_release_dir)
    return {
        **report,
        "checksum_inventory_sha256": checksum_inventory_sha256,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("data/sources/official_docx"),
    )
    parser.add_argument(
        "--base-corpus",
        type=Path,
        default=Path("data/processed/vbpl_articles_raw.json"),
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=Path("data/raw/official_docx"),
    )
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=Path(
            "data/releases/labor-law-2026-07-27-candidate"
        ),
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--local-files-only",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--expected-model-max-tokens",
        type=int,
        default=DEFAULT_MODEL_MAX_TOKENS,
    )
    parser.add_argument(
        "--target-tokens",
        type=int,
        default=DEFAULT_TARGET_TOKENS,
    )
    parser.add_argument(
        "--operational-max-tokens",
        type=int,
        default=DEFAULT_OPERATIONAL_MAX_TOKENS,
        help=(
            "Maximum exact E5 passage tokens per chunk. Must remain below "
            "the production indexer's near-limit threshold of 480."
        ),
    )
    parser.add_argument(
        "--tokenizer-threads",
        type=int,
        default=DEFAULT_TOKENIZER_THREADS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_release(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
