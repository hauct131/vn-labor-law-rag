#!/usr/bin/env python3
"""Fail-closed Word ingestion from the official Vietnamese Gazette.

This adapter deliberately gives the two official sources different roles:

* ``congbao.chinhphu.vn`` supplies canonical DOCX/DOC bytes and Gazette facts;
* ``vanban.chinhphu.vn`` supplies identity metadata for cross-checking only;
* current legal status, expiry and amendment relations remain review fields.

DOCX is preferred. Legacy DOC is retained byte-for-byte and normalized to
DOCX with LibreOffice. PDF links are recorded for QA but never fetched.

The implementation reuses the production primitives from ``vbpl_portal``:
bounded retry/backoff, per-document locking, atomic writes, immutable snapshot
publication, SHA-256 manifests and exact document-number normalization.
"""
from __future__ import annotations

import argparse
import html.parser
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from xml.etree import ElementTree as ET

try:  # import works both as ``python scripts/...`` and in pytest
    from scripts import vbpl_portal as common
except ImportError:  # pragma: no cover - direct script execution
    import vbpl_portal as common  # type: ignore[no-redef]


SNAPSHOT_SCHEMA = "congbao-word-source-snapshot-v3"
RUN_SCHEMA = "congbao-word-ingestion-run-v2"
DEFAULT_CONFIG = Path("config/vbpl_corpus.json")
DEFAULT_OUTPUT = Path("data/raw/official_docx")
DEFAULT_SOURCE_DIR = Path("data/sources/official_docx")
ALLOWED_PAGE_HOSTS = {"congbao.chinhphu.vn", "vanban.chinhphu.vn"}
ALLOWED_DOWNLOAD_HOSTS = {"congbao.chinhphu.vn"}
ALLOWED_DOWNLOAD_SUFFIXES = (".chinhphu.vn", ".cdnchinhphu.vn")
DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
DOC_CONTENT_TYPE = "application/msword"
LEGACY_DOC_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
REQUIRED_DOCX_MEMBERS = {"[Content_Types].xml", "word/document.xml"}


class CongbaoError(RuntimeError):
    """Controlled Gazette ingestion failure."""


class CongbaoUnavailableError(CongbaoError):
    """The Gazette record has no usable canonical Word attachment."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class CongbaoToolingError(CongbaoError):
    """Local normalization tooling is unavailable or failed."""


@dataclass(frozen=True)
class DocumentSpec:
    document_number: str
    canonical_document_id: str
    title: str
    source_file: str
    gazette_page_url: str
    metadata_page_url: str | None
    expected_articles: int | None


@dataclass(frozen=True)
class Link:
    url: str
    advertised_name: str
    kind: str


@dataclass(frozen=True)
class PreparedWordPart:
    link: Link
    original_bytes: bytes
    normalized_docx: bytes
    visible_text: str
    members: Mapping[str, str]
    backslash_count: int


class _PageParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self.headings: list[str] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self._heading_depth = 0
        self._heading_text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.casefold()
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor_text = []
        if tag in {"h1", "h2", "h3"}:
            self._heading_depth += 1
            self._heading_text = []

    def handle_data(self, data: str) -> None:
        value = _clean_text(data)
        if not value:
            return
        self.text.append(value)
        if self._href is not None:
            self._anchor_text.append(value)
        if self._heading_depth:
            self._heading_text.append(value)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "a" and self._href is not None:
            self.links.append((self._href, _clean_text(" ".join(self._anchor_text))))
            self._href = None
            self._anchor_text = []
        if tag in {"h1", "h2", "h3"} and self._heading_depth:
            heading = _clean_text(" ".join(self._heading_text))
            if heading:
                self.headings.append(heading)
            self._heading_depth -= 1
            self._heading_text = []


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).replace("\u00a0", " ")).strip()


def _host_allowed(host: str, *, download: bool) -> bool:
    host = host.casefold().rstrip(".")
    if not download:
        return host in ALLOWED_PAGE_HOSTS
    return host in ALLOWED_DOWNLOAD_HOSTS or any(
        host.endswith(suffix) for suffix in ALLOWED_DOWNLOAD_SUFFIXES
    )


def validate_url(url: str, *, download: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise CongbaoError(f"Only absolute HTTPS URLs are allowed: {url}")
    if not _host_allowed(parsed.hostname, download=download):
        role = "download" if download else "page"
        raise CongbaoError(f"Untrusted {role} host: {parsed.hostname!r}")


def _candidate_name(href: str, text: str) -> str | None:
    candidates = [text, unquote(href)]
    query = parse_qs(urlparse(href).query)
    candidates.extend(query.get("filename", []))
    candidates.extend(query.get("file_name", []))
    for value in candidates:
        match = re.search(
            r"([^?&#]+\.(?:docx|doc|pdf))(?:$|[?&#])", value, re.I
        )
        if match:
            return _clean_text(match.group(1))
    return None


def parse_page(page_url: str, raw_html: bytes) -> tuple[_PageParser, list[Link]]:
    parser = _PageParser()
    parser.feed(raw_html.decode("utf-8", errors="replace"))
    links: list[Link] = []
    seen: set[str] = set()
    for href, text in parser.links:
        name = _candidate_name(href, text)
        if not name:
            continue
        url = urljoin(page_url, href)
        validate_url(url, download=True)
        if url in seen:
            continue
        seen.add(url)
        suffix = Path(name).suffix.casefold()
        kind = suffix.lstrip(".")
        links.append(Link(url=url, advertised_name=name, kind=kind))
    return parser, links


def select_exact_docx(links: Sequence[Link], document_number: str) -> Link:
    docx_links = [link for link in links if link.kind == "docx"]
    if not docx_links:
        raise CongbaoError("Gazette page exposes no DOCX link")
    expected = common.normalize_document_number(document_number)
    exact = [
        link
        for link in docx_links
        if expected in common.normalize_document_number(link.advertised_name)
    ]
    if len(exact) == 1:
        return exact[0]
    if not exact and len(docx_links) == 1:
        return docx_links[0]
    raise CongbaoError(
        "DOCX link selection is ambiguous for "
        f"{document_number}: {[link.advertised_name for link in docx_links]}"
    )


def select_exact_word_parts(
    links: Sequence[Link], document_number: str
) -> list[Link]:
    """Select every exact Word part, preferring DOCX over legacy DOC.

    One legal document can span multiple Gazette issues, so multiple exact
    links are valid and their page order is preserved.  PDF is deliberately
    excluded because this adapter never performs OCR.
    """

    expected = common.normalize_document_number(document_number)
    word_links = [link for link in links if link.kind in {"docx", "doc"}]
    exact = [
        link
        for link in word_links
        if expected in common.normalize_document_number(link.advertised_name)
    ]
    candidates = exact or (word_links if len(word_links) == 1 else [])
    if not candidates:
        raise CongbaoUnavailableError(
            "word_attachment_not_found",
            f"Gazette page exposes no exact Word attachment for {document_number}",
        )
    preferred_kind = "docx" if any(
        link.kind == "docx" for link in candidates
    ) else "doc"
    selected = [link for link in candidates if link.kind == preferred_kind]
    if not selected:
        raise CongbaoUnavailableError(
            "word_attachment_not_found",
            f"Gazette page exposes no supported Word attachment for {document_number}",
        )
    return selected


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", value)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def _next_value(tokens: Sequence[str], labels: Iterable[str]) -> str | None:
    normalized_labels = {_clean_text(label).casefold() for label in labels}
    for index, token in enumerate(tokens):
        if _clean_text(token).casefold().rstrip(":") not in normalized_labels:
            continue
        for candidate in tokens[index + 1 : index + 5]:
            value = _clean_text(candidate)
            if value and value.casefold().rstrip(":") not in normalized_labels:
                return value
    return None


def extract_page_metadata(
    parser: _PageParser, *, source: str
) -> dict[str, Any]:
    tokens = parser.text
    flattened = _clean_text(" ".join(tokens))
    metadata: dict[str, Any] = {
        "document_number": _next_value(tokens, {"Số ký hiệu", "Số, ký hiệu"}),
        "document_type": _next_value(tokens, {"Loại văn bản"}),
        "issuing_authority": _next_value(tokens, {"Cơ quan ban hành"}),
        "signer": _next_value(tokens, {"Người ký"}),
        "summary": _next_value(tokens, {"Trích yếu"}),
        "source": source,
    }
    issued = _next_value(tokens, {"Ngày ban hành"})
    effective = _next_value(tokens, {"Ngày có hiệu lực", "Ngày hiệu lực"})
    if source == "congbao":
        top = re.search(
            r"Ban hành:\s*(\d{1,2}/\d{1,2}/\d{4})"
            r"(?:\s*-\s*Hiệu lực:\s*(\d{1,2}/\d{1,2}/\d{4}))?",
            flattened,
            re.I,
        )
        if top:
            issued = issued or top.group(1)
            effective = effective or top.group(2)
        gazette = re.search(
            r"(?:Nằm trong các Công báo|Công báo(?: số)?)\s*:?\s*(\d+(?:\s*\+\s*\d+)*)",
            flattened,
            re.I,
        )
        metadata["gazette_issue"] = gazette.group(1) if gazette else None
    metadata["issued_at"] = _iso_date(issued)
    metadata["effective_from"] = _iso_date(effective)
    metadata["title"] = parser.headings[0] if parser.headings else None
    return metadata


def normalized_docx_members(value: bytes) -> tuple[dict[str, str], int]:
    if value.startswith(b"%PDF"):
        raise CongbaoError("PDF bytes rejected by DOCX-only policy")
    if value.lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise CongbaoError("HTML bytes rejected as a DOCX attachment")
    if not zipfile.is_zipfile(io.BytesIO(value)):
        raise CongbaoError("Downloaded attachment is not ZIP/OOXML")
    with zipfile.ZipFile(io.BytesIO(value)) as archive:
        if archive.testzip() is not None:
            raise CongbaoError("DOCX ZIP contains a corrupt member")
        mapping: dict[str, str] = {}
        backslashes = 0
        for raw in archive.namelist():
            normalized = raw.replace("\\", "/")
            if normalized != raw:
                backslashes += 1
            path = PurePosixPath(normalized)
            if path.is_absolute() or ".." in path.parts:
                raise CongbaoError(f"Unsafe DOCX member path: {raw!r}")
            if normalized in mapping:
                raise CongbaoError(
                    f"Ambiguous member after path normalization: {normalized!r}"
                )
            mapping[normalized] = raw
        missing = REQUIRED_DOCX_MEMBERS - set(mapping)
        if missing:
            raise CongbaoError(f"DOCX misses required OOXML members: {sorted(missing)}")
    return mapping, backslashes


def validate_legacy_doc(value: bytes) -> None:
    """Reject HTML/PDF/error pages masquerading as a legacy Word file."""

    if not value.startswith(LEGACY_DOC_MAGIC):
        raise CongbaoError(
            "Legacy .doc attachment does not have the OLE Compound File signature"
        )


def convert_legacy_doc_to_docx(
    value: bytes,
    *,
    libreoffice_bin: str | None = None,
) -> bytes:
    """Normalize an immutable Gazette .doc with a fixed LibreOffice command."""

    validate_legacy_doc(value)
    executable = (
        libreoffice_bin
        or os.environ.get("LIBREOFFICE_BIN")
        or shutil.which("libreoffice")
        or shutil.which("soffice")
    )
    if not executable:
        raise CongbaoToolingError(
            "LibreOffice/soffice is required to normalize Gazette .doc files"
        )
    with tempfile.TemporaryDirectory(prefix="congbao-doc-") as temp_name:
        temp = Path(temp_name)
        source = temp / "source.doc"
        source.write_bytes(value)
        completed = subprocess.run(
            [
                executable,
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                str(temp),
                str(source),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        output = temp / "source.docx"
        if completed.returncode != 0 or not output.is_file():
            detail = _clean_text(
                completed.stderr.decode("utf-8", errors="replace")
                or completed.stdout.decode("utf-8", errors="replace")
            )
            raise CongbaoToolingError(
                "LibreOffice failed to normalize Gazette .doc"
                + (f": {detail[:300]}" if detail else "")
            )
        normalized = output.read_bytes()
    normalized_docx_members(normalized)
    return normalized


def prepare_word_part(
    link: Link,
    response: common.HttpResponse,
) -> PreparedWordPart:
    if link.kind == "docx":
        normalized = response.body
    elif link.kind == "doc":
        normalized = convert_legacy_doc_to_docx(response.body)
    else:  # pragma: no cover - protected by select_exact_word_parts
        raise CongbaoError(f"Unsupported Word attachment kind: {link.kind}")
    members, backslash_count = normalized_docx_members(normalized)
    visible_text = docx_visible_text(normalized, members)
    return PreparedWordPart(
        link=link,
        original_bytes=response.body,
        normalized_docx=normalized,
        visible_text=visible_text,
        members=members,
        backslash_count=backslash_count,
    )


def docx_visible_text(value: bytes, members: Mapping[str, str]) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(value)) as archive:
            root = ET.fromstring(archive.read(members["word/document.xml"]))
    except (KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise CongbaoError(f"Cannot parse word/document.xml: {exc}") from exc
    text = " ".join(
        node.text or ""
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "t"
    )
    return _clean_text(text)


def _merge_metadata(
    spec: DocumentSpec,
    gazette: Mapping[str, Any],
    government: Mapping[str, Any] | None,
    docx_text: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sources = [gazette] + ([government] if government else [])
    expected = common.normalize_document_number(spec.document_number)
    observed_numbers = [
        str(source.get("document_number"))
        for source in sources
        if source and source.get("document_number")
    ]
    conflicts = [
        value
        for value in observed_numbers
        if common.normalize_document_number(value) != expected
    ]
    if conflicts:
        raise CongbaoError(
            f"Metadata document-number conflict: expected {spec.document_number}, "
            f"observed {conflicts}"
        )
    for field in ("issued_at", "effective_from"):
        observed = {
            str(source[field])
            for source in sources
            if source and source.get(field)
        }
        if len(observed) > 1:
            raise CongbaoError(
                f"Cross-source metadata conflict for {field}: {sorted(observed)}"
            )
    if expected not in common.normalize_document_number(docx_text):
        raise CongbaoError(
            f"DOCX body does not contain exact document number {spec.document_number}"
        )

    def pick(field: str, *ordered: Mapping[str, Any] | None) -> Any:
        for item in ordered:
            if item and item.get(field) not in {None, ""}:
                return item[field]
        return None

    government = government or {}
    document = {
        "document_number": spec.document_number,
        "canonical_document_id": spec.canonical_document_id,
        "title": pick("title", government, gazette) or spec.title,
        "document_type": pick("document_type", government, gazette),
        "issuing_authority": pick("issuing_authority", government, gazette),
        "signer": pick("signer", government, gazette),
        "summary": pick("summary", government, gazette) or spec.title,
        "issued_at": pick("issued_at", government, gazette),
        "effective_from": pick("effective_from", government, gazette),
        "effective_to": None,
        "legal_status": None,
        "gazette_issue": gazette.get("gazette_issue"),
        "relations": None,
    }
    field_provenance = {
        "document_number": {
            "value": spec.document_number,
            "sources": ["config", "congbao_page", "docx_body"]
            + (
                ["government_metadata_page"]
                if government.get("document_number")
                else []
            ),
            "status": "cross_checked",
        },
        "canonical_content": {
            "source": "congbao_docx",
            "status": "hash_bound_original_bytes",
        },
        "issued_at": {
            "source": "government_metadata_page_or_congbao_page",
            "status": "observed" if document["issued_at"] else "missing",
        },
        "effective_from": {
            "source": "government_metadata_page_or_congbao_page",
            "status": "observed" if document["effective_from"] else "missing",
        },
        "gazette_issue": {
            "source": "congbao_page",
            "status": "observed" if document["gazette_issue"] else "missing",
        },
        "effective_to": {
            "source": None,
            "status": "requires_legal_effect_review",
        },
        "legal_status": {
            "source": None,
            "status": "requires_vbpl_or_legal_effect_review",
        },
        "relations": {
            "source": None,
            "status": "requires_vbpl_or_legal_effect_review",
        },
    }
    return document, field_provenance


def load_specs(config_path: Path) -> list[DocumentSpec]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    specs: list[DocumentSpec] = []
    for item in payload.get("documents", []):
        policy = item.get("canonical_source_policy") or {}
        preferred = policy.get("preferred_adapter")
        is_legacy_docx_source = (
            item.get("source_adapter") == "official_government_docx"
        )
        if preferred != "official_gazette_word" and not is_legacy_docx_source:
            continue
        gazette_url = item.get("gazette_page_url")
        if not gazette_url:
            # A declared not-found Gazette record is handled by the canonical
            # source-policy runner, which invokes the VBPL fallback.
            if policy.get("preferred_status") == "not_found":
                continue
            raise CongbaoError(
                f"{item.get('document_number')}: gazette_page_url is required"
            )
        source_file = item.get("source_file") or (
            f"{common.safe_slug(str(item['document_number']))}.docx"
        )
        specs.append(
            DocumentSpec(
                document_number=str(item["document_number"]),
                canonical_document_id=str(item["canonical_document_id"]),
                title=str(item["title"]),
                source_file=str(source_file),
                gazette_page_url=str(gazette_url),
                metadata_page_url=(
                    str(item["official_page_url"])
                    if item.get("official_page_url")
                    else None
                ),
                expected_articles=(
                    int(item["expected_articles"])
                    if item.get("expected_articles") is not None
                    else None
                ),
            )
        )
    if not specs:
        raise CongbaoError("Config contains no official DOCX Gazette sources")
    return specs


def _contract(spec: DocumentSpec) -> dict[str, Any]:
    contract = {
        "schema_version": SNAPSHOT_SCHEMA,
        "document_number": spec.document_number,
        "canonical_document_id": spec.canonical_document_id,
        "source_file": spec.source_file,
        "gazette_page_url": spec.gazette_page_url,
        "metadata_page_url": spec.metadata_page_url,
        "document_attachment_policy": "word_required_docx_then_doc_no_ocr",
    }
    encoded = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {"contract": contract, "sha256": common.sha256_bytes(encoded)}


def _content_type(response: common.HttpResponse) -> str:
    for key, value in response.headers.items():
        if key.casefold() == "content-type":
            return value.split(";", 1)[0].strip().casefold()
    return ""


def verify_snapshot(
    snapshot: Path, *, expected_document_number: str | None = None
) -> dict[str, Any]:
    manifest_path = snapshot / "manifest.json"
    checksum_path = snapshot / "SHA256SUMS.txt"
    if not manifest_path.is_file() or not checksum_path.is_file():
        raise CongbaoError(f"Incomplete snapshot: {snapshot}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA:
        raise CongbaoError("Unsupported Gazette snapshot schema")
    actual = str((manifest.get("document") or {}).get("document_number") or "")
    if expected_document_number and (
        common.normalize_document_number(actual)
        != common.normalize_document_number(expected_document_number)
    ):
        raise CongbaoError(f"Snapshot identity mismatch: {actual!r}")
    gates = manifest.get("gates") or {}
    failed = sorted(name for name, passed in gates.items() if passed is not True)
    if not gates or failed:
        raise CongbaoError(f"Snapshot has failed gates: {failed or ['missing_gates']}")
    checksums = common.parse_checksums(checksum_path)
    source = manifest.get("source") or {}
    normalized_paths = list(source.get("snapshot_docx_paths") or [])
    if not normalized_paths and source.get("snapshot_docx_path"):
        normalized_paths = [str(source["snapshot_docx_path"])]
    original_paths = list(source.get("snapshot_original_paths") or [])
    required = {
        "manifest.json",
        "portal/congbao_page.html",
        *normalized_paths,
        *original_paths,
    }
    required.discard("")
    if not normalized_paths:
        raise CongbaoError("Snapshot does not declare normalized DOCX parts")
    if not required.issubset(checksums):
        raise CongbaoError(
            f"Checksum manifest misses critical files: {sorted(required - set(checksums))}"
        )
    mismatches = [
        relative
        for relative, digest in checksums.items()
        if not (snapshot / relative).is_file()
        or common.sha256_file(snapshot / relative) != digest
    ]
    if mismatches:
        raise CongbaoError(f"Snapshot checksum mismatch: {mismatches}")
    return {
        "valid": True,
        "snapshot": str(snapshot),
        "document_number": actual,
        "files_verified": len(checksums),
    }


def _source_fingerprint(parts: Sequence[PreparedWordPart]) -> str:
    payload = [
        {
            "url": part.link.url,
            "kind": part.link.kind,
            "original_sha256": common.sha256_bytes(part.original_bytes),
            "normalized_docx_sha256": common.sha256_bytes(
                part.normalized_docx
            ),
        }
        for part in parts
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return common.sha256_bytes(encoded)


def _latest_matching_snapshot(
    output_root: Path, spec: DocumentSpec, source_fingerprint: str
) -> Path | None:
    root = output_root / common.safe_slug(spec.document_number)
    if not root.is_dir():
        return None
    for candidate in sorted(root.iterdir(), reverse=True):
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        try:
            verify_snapshot(candidate, expected_document_number=spec.document_number)
            manifest = json.loads(
                (candidate / "manifest.json").read_text(encoding="utf-8")
            )
            if (
                (manifest.get("content_hashes") or {}).get("source_fingerprint_sha256")
                == source_fingerprint
                and manifest.get("ingestion_contract_sha256")
                == _contract(spec)["sha256"]
            ):
                return candidate
        except Exception:
            continue
    return None


def _materialize_source(
    value: bytes, target: Path, *, replace_source: bool
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = common.sha256_bytes(value)
    if target.exists():
        if common.sha256_file(target) == digest:
            return
        if not replace_source:
            raise CongbaoError(
                f"Canonical source file changed: {target}. Review the new snapshot "
                "and rerun with --replace-source."
            )
    common.atomic_write_bytes(target, value)


def _materialized_targets(
    spec: DocumentSpec,
    parts: Sequence[PreparedWordPart],
    source_dir: Path,
) -> list[Path]:
    if len(parts) == 1:
        return [source_dir / spec.source_file]
    stem = Path(spec.source_file).stem
    return [
        source_dir / common.safe_slug(spec.document_number) / f"{stem}.part-{index:02d}.docx"
        for index in range(1, len(parts) + 1)
    ]


def _snapshot_original_name(index: int, link: Link) -> str:
    return f"part-{index:02d}.{link.kind}"


def ingest_one(
    spec: DocumentSpec,
    *,
    client: common.RetryingHttpClient,
    output_root: Path,
    source_dir: Path,
    replace_source: bool = False,
    force_snapshot: bool = False,
) -> dict[str, Any]:
    validate_url(spec.gazette_page_url)
    if spec.metadata_page_url:
        validate_url(spec.metadata_page_url)
    with common.document_lock(output_root, spec.document_number):
        gazette_response = client.fetch(spec.gazette_page_url, accept="text/html")
        if _content_type(gazette_response) not in {"text/html", "application/xhtml+xml"}:
            raise CongbaoError("Gazette detail page did not return HTML")
        gazette_parser, links = parse_page(
            gazette_response.final_url, gazette_response.body
        )
        selected = select_exact_word_parts(links, spec.document_number)
        attachment_responses: list[common.HttpResponse] = []
        parts: list[PreparedWordPart] = []
        for link in selected:
            attachment_response = client.fetch(
                link.url,
                accept=(
                    DOCX_CONTENT_TYPE
                    if link.kind == "docx"
                    else DOC_CONTENT_TYPE
                ),
                referer=spec.gazette_page_url,
            )
            validate_url(attachment_response.final_url, download=True)
            attachment_responses.append(attachment_response)
            parts.append(prepare_word_part(link, attachment_response))
        visible_text = " ".join(part.visible_text for part in parts)

        government_response: common.HttpResponse | None = None
        government_meta: dict[str, Any] | None = None
        if spec.metadata_page_url:
            government_response = client.fetch(
                spec.metadata_page_url, accept="text/html"
            )
            if _content_type(government_response) not in {
                "text/html",
                "application/xhtml+xml",
            }:
                raise CongbaoError("Government metadata page did not return HTML")
            government_parser, _ = parse_page(
                government_response.final_url, government_response.body
            )
            government_meta = extract_page_metadata(
                government_parser, source="government_portal"
            )

        gazette_meta = extract_page_metadata(gazette_parser, source="congbao")
        document, field_provenance = _merge_metadata(
            spec, gazette_meta, government_meta, visible_text
        )
        fingerprint = _source_fingerprint(parts)
        targets = _materialized_targets(spec, parts, source_dir)
        existing = None if force_snapshot else _latest_matching_snapshot(
            output_root, spec, fingerprint
        )
        if existing:
            for part, target in zip(parts, targets, strict=True):
                _materialize_source(
                    part.normalized_docx,
                    target,
                    replace_source=replace_source,
                )
            manifest = json.loads(
                (existing / "manifest.json").read_text(encoding="utf-8")
            )
            return {
                "status": "unchanged",
                "document_number": spec.document_number,
                "snapshot_dir": str(existing),
                "source_path": str(targets[0]),
                "source_paths": [str(target) for target in targets],
                "manifest": manifest,
            }

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        doc_root = output_root / common.safe_slug(spec.document_number)
        doc_root.mkdir(parents=True, exist_ok=True)
        final = doc_root / f"{timestamp}-{fingerprint[:8]}"
        if final.exists():
            raise CongbaoError(f"Snapshot already exists: {final}")
        staging = doc_root / f".tmp-{timestamp}-{uuid.uuid4().hex[:8]}"
        try:
            (staging / "portal").mkdir(parents=True)
            snapshot_originals: list[str] = []
            snapshot_docx_parts: list[str] = []
            for index, part in enumerate(parts, start=1):
                original = Path("document") / "original" / _snapshot_original_name(
                    index, part.link
                )
                normalized = (
                    Path("document") / "normalized" / f"part-{index:02d}.docx"
                )
                common.atomic_write_bytes(staging / original, part.original_bytes)
                common.atomic_write_bytes(
                    staging / normalized, part.normalized_docx
                )
                snapshot_originals.append(original.as_posix())
                snapshot_docx_parts.append(normalized.as_posix())
            common.atomic_write_bytes(
                staging / "portal" / "congbao_page.html", gazette_response.body
            )
            if government_response is not None:
                common.atomic_write_bytes(
                    staging / "portal" / "government_metadata_page.html",
                    government_response.body,
                )
            contract = _contract(spec)
            pdf_links = [link.url for link in links if link.kind == "pdf"]
            gates = {
                "exact_document_number": True,
                "word_parts_selected": bool(parts),
                "normalized_docx_wordprocessingml_valid": True,
                "normalized_docx_body_identity_valid": True,
                "canonical_bytes_hash_bound": True,
                "no_pdf_downloaded": True,
                "metadata_identity_consistent": True,
                "official_pages_persisted": True,
            }
            manifest = {
                "schema_version": SNAPSHOT_SCHEMA,
                "review_status": "staged_unapproved",
                "retrieved_at": common.utc_now(),
                "ingestion_contract": contract["contract"],
                "ingestion_contract_sha256": contract["sha256"],
                "source": {
                    "provider": "Công báo điện tử Chính phủ",
                    "acquisition_method": "congbao_word",
                    "canonical_content_source": "official_gazette_word",
                    "metadata_source": "government_portal_plus_congbao",
                    "gazette_page_url": spec.gazette_page_url,
                    "government_metadata_page_url": spec.metadata_page_url,
                    "advertised_word_urls": [link.url for link in selected],
                    "final_word_urls": [
                        response.final_url for response in attachment_responses
                    ],
                    "source_formats": [link.kind for link in selected],
                    "snapshot_original_paths": snapshot_originals,
                    "snapshot_docx_paths": snapshot_docx_parts,
                    "snapshot_docx_path": (
                        snapshot_docx_parts[0] if len(parts) == 1 else None
                    ),
                    "materialized_source_paths": [str(path) for path in targets],
                    "materialized_source_path": str(targets[0]),
                    "docx_only": all(link.kind == "docx" for link in selected),
                    "legacy_doc_normalized": any(
                        link.kind == "doc" for link in selected
                    ),
                    "pdf_downloaded": False,
                    "pdf_verification_links": pdf_links,
                    "operations": [
                        "FetchGazettePage",
                        "ResolveExactWordParts",
                        "DownloadWordParts",
                        "NormalizeLegacyDocToDocxIfNeeded",
                        "ValidateWordprocessingML",
                        "FetchGovernmentMetadataPage",
                        "CrossCheckDocumentIdentity",
                        "PublishImmutableSnapshot",
                    ],
                },
                "document": document,
                "field_provenance": field_provenance,
                "observed_metadata": {
                    "congbao": gazette_meta,
                    "government_portal": government_meta,
                },
                "package": {
                    "part_count": len(parts),
                    "parts": [
                        {
                            "index": index,
                            "source_format": part.link.kind,
                            "zip_member_count": len(part.members),
                            "nonconformant_backslash_member_count": (
                                part.backslash_count
                            ),
                            "in_memory_member_path_normalization": (
                                part.backslash_count > 0
                            ),
                        }
                        for index, part in enumerate(parts, start=1)
                    ],
                },
                "http": {
                    "gazette_page_attempts": gazette_response.attempts,
                    "gazette_page_elapsed_ms": round(
                        gazette_response.elapsed_ms, 3
                    ),
                    "word_parts": [
                        {
                            "attempts": response.attempts,
                            "elapsed_ms": round(response.elapsed_ms, 3),
                            "content_type": _content_type(response),
                        }
                        for response in attachment_responses
                    ],
                },
                "content_hashes": {
                    "source_fingerprint_sha256": fingerprint,
                    "original_attachment_sha256s": [
                        common.sha256_bytes(part.original_bytes)
                        for part in parts
                    ],
                    "normalized_docx_sha256s": [
                        common.sha256_bytes(part.normalized_docx)
                        for part in parts
                    ],
                    "original_docx_sha256": (
                        common.sha256_bytes(parts[0].normalized_docx)
                        if len(parts) == 1
                        else None
                    ),
                    "docx_visible_text_sha256": common.sha256_bytes(
                        visible_text.encode("utf-8")
                    ),
                    "congbao_page_sha256": common.sha256_bytes(
                        gazette_response.body
                    ),
                    "government_metadata_page_sha256": (
                        common.sha256_bytes(government_response.body)
                        if government_response is not None
                        else None
                    ),
                },
                "limitations": [
                    "No PDF attachment was downloaded by this adapter.",
                    "Legacy DOC normalization is derived; original Gazette "
                    "bytes and normalized DOCX hashes are both retained.",
                    "DOCX package signatures are detected downstream; "
                    "cryptographic validation is not claimed here.",
                    "effective_to, current legal_status and amendment "
                    "relations require VBPL or explicit legal-effect review.",
                ],
                "gates": gates,
            }
            common.write_json(staging / "manifest.json", manifest)
            common.write_checksums(staging)
            verify_snapshot(staging, expected_document_number=spec.document_number)
            for part, target in zip(parts, targets, strict=True):
                _materialize_source(
                    part.normalized_docx,
                    target,
                    replace_source=replace_source,
                )
            os.replace(staging, final)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return {
            "status": "created",
            "document_number": spec.document_number,
            "snapshot_dir": str(final),
            "source_path": str(targets[0]),
            "source_paths": [str(target) for target in targets],
            "manifest": manifest,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--backoff-seconds", type=float, default=1.0)
    parser.add_argument("--max-mib", type=int, default=100)
    parser.add_argument("--replace-source", action="store_true")
    parser.add_argument("--force-snapshot", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch = subparsers.add_parser("fetch", help="Fetch one configured document")
    fetch.add_argument("document_number")
    batch = subparsers.add_parser(
        "fetch-config", help="Fetch every configured Gazette Word source"
    )
    batch.add_argument(
        "--run-report", type=Path, default=Path("data/raw/official_docx/run_manifest.json")
    )
    batch.add_argument("--continue-on-error", action="store_true")
    verify = subparsers.add_parser("verify", help="Verify an existing snapshot")
    verify.add_argument("snapshot", type=Path)
    verify.add_argument("--document-number")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "verify":
        try:
            result = verify_snapshot(
                args.snapshot, expected_document_number=args.document_number
            )
        except Exception as exc:
            print(f"CONGBAO VERIFY FAILED: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.timeout <= 0 or args.retries <= 0 or args.max_mib <= 0:
        print("timeout, retries and max-mib must be positive", file=sys.stderr)
        return 2
    try:
        specs = load_specs(args.config)
        if args.command == "fetch":
            expected = common.normalize_document_number(args.document_number)
            specs = [
                spec
                for spec in specs
                if common.normalize_document_number(spec.document_number) == expected
            ]
            if len(specs) != 1:
                raise CongbaoError(
                    f"Configured document not found or ambiguous: {args.document_number}"
                )
        client = common.RetryingHttpClient(
            timeout=args.timeout,
            retries=args.retries,
            backoff_seconds=args.backoff_seconds,
            max_bytes=args.max_mib * 1024 * 1024,
        )
        results: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        for spec in specs:
            try:
                result = ingest_one(
                    spec,
                    client=client,
                    output_root=args.output,
                    source_dir=args.source_dir,
                    replace_source=args.replace_source,
                    force_snapshot=args.force_snapshot,
                )
                results.append(result)
                print(
                    f"{result['status'].upper()}: {spec.document_number} -> "
                    f"{result['source_path']}"
                )
            except Exception as exc:
                failures.append(
                    {
                        "document_number": spec.document_number,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                print(
                    f"FAILED: {spec.document_number}: {exc}", file=sys.stderr
                )
                if args.command == "fetch" or not args.continue_on_error:
                    break
        if args.command == "fetch-config":
            report = {
                "schema_version": RUN_SCHEMA,
                "generated_at": common.utc_now(),
                "config": str(args.config),
                "word_only_no_pdf": True,
                "documents_requested": len(specs),
                "documents_succeeded": len(results),
                "documents_failed": len(failures),
                "results": [
                    {
                        "document_number": result["document_number"],
                        "status": result["status"],
                        "snapshot_dir": result["snapshot_dir"],
                        "source_path": result["source_path"],
                        "source_paths": result.get("source_paths", []),
                        "sha256": result["manifest"]["content_hashes"][
                            "source_fingerprint_sha256"
                        ],
                    }
                    for result in results
                ],
                "failures": failures,
                "status": "PASS" if not failures else "FAIL",
            }
            common.write_json(args.run_report, report)
            print(f"Run report: {args.run_report}")
        return 1 if failures else 0
    except Exception as exc:
        print(f"CONGBAO INGESTION FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
