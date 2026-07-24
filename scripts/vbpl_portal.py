#!/usr/bin/env python3
"""Production-safe VBPL ingestion for the Vietnamese labor-law RAG project.

The command fetches one or more official documents from ``vbpl.vn`` and writes
immutable, checksum-bound snapshots.  It prefers the public document gateway
when reachable and falls back to a real Playwright browser when the portal
requires a browser-minted session.  A snapshot is published only after all
fail-closed gates and checksum verification pass.

Important properties:
* exact document-number and item-id validation;
* JSON/XML response support;
* bounded retries with Retry-After and exponential backoff;
* sitemap caching, per-document locking, resume, and batch run manifests;
* raw response preservation and truthful acquisition provenance;
* explicit attachment policy (no vacuous ``all([]) == True`` success);
* atomic temporary-directory -> final-snapshot publication;
* checksum verification before resume or downstream use.

The crawler does not bypass CAPTCHA, persist bearer tokens, or attempt to evade
portal access controls.  Use ``--headed`` when a normal browser interaction is
required, and contact the Ministry of Justice for bulk access if appropriate.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import logging
import os
import random
import re
import shutil
import sys
import time
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from lxml import html as lxml_html

LOGGER = logging.getLogger("vbpl_portal")

DEFAULT_WEBSITE = "https://vbpl.vn/"
DEFAULT_SITEMAP = "https://vbpl.vn/sitemap.xml"
DEFAULT_API_SUBSTRING = "/api/qtdc/public/doc/"
DEFAULT_GATEWAY_TEMPLATE = (
    "https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/{item_id}"
)
DEFAULT_TIMEOUT = 45.0
DEFAULT_MAX_BYTES = 256 * 1024 * 1024
DEFAULT_RETRIES = 4
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_SITEMAP_CACHE_TTL_HOURS = 24.0
DEFAULT_LOCK_STALE_SECONDS = 2 * 60 * 60
RETRY_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
ATTACHMENT_POLICIES = {"ignore", "best_effort", "required_if_listed", "required"}
SNAPSHOT_SCHEMA = "vbpl-source-snapshot-v3"
RUN_SCHEMA = "vbpl-ingestion-run-v1"
INGESTION_CONTRACT_SCHEMA = "vbpl-ingestion-contract-v1"


class VbplPortalError(RuntimeError):
    """Base error for portal ingestion."""


class VbplPortalNotFound(VbplPortalError):
    """Raised when no exact document URL can be resolved."""


class VbplPortalAmbiguous(VbplPortalError):
    """Raised when more than one equally exact document URL is found."""


class VbplPortalLockError(VbplPortalError):
    """Raised when another process is already ingesting the document."""


@dataclass(frozen=True)
class HttpResponse:
    requested_url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes
    attempts: int
    elapsed_ms: float


@dataclass
class CaptureRecord:
    url: str
    status: int
    content_type: str
    body: bytes
    payload: Any
    acquisition: str
    attempts: int = 1
    elapsed_ms: float = 0.0
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PortalDocument:
    document_number: str
    title: str
    full_text_html: str
    issued_at: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    legal_status: str | None = None
    issuing_authority: str | None = None
    item_id: int | str | None = None
    type_vb: Any = None
    appendix_html: str = ""
    attachment_inventory: tuple[dict[str, Any], ...] = ()


@dataclass
class BrowserSession:
    """Reusable Playwright browser/context for a batch run."""

    headed: bool
    timeout: float
    settle_seconds: float
    api_substring: str
    _playwright: Any = None
    _browser: Any = None
    _context: Any = None
    _last_authorization: str | None = None

    def _ensure_started(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise VbplPortalError(
                "Playwright is required for browser fallback. Install "
                "requirements-corpus.txt and run `playwright install chromium`."
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=not self.headed)
        self._context = self._browser.new_context(
            locale="vi-VN",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/124 Safari/537.36"
            ),
        )
        self._context.set_default_timeout(int(self.timeout * 1000))

    def __enter__(self) -> "BrowserSession":
        self._ensure_started()
        return self

    @property
    def is_started(self) -> bool:
        return self._context is not None

    def close(self) -> None:
        for resource in (self._context, self._browser):
            try:
                if resource is not None:
                    resource.close()
            except Exception:
                LOGGER.exception("Failed to close Playwright resource")
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                LOGGER.exception("Failed to stop Playwright")
        self._context = None
        self._browser = None
        self._playwright = None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def fetch_detail(self, detail_url: str) -> tuple[str, list[CaptureRecord]]:
        self._ensure_started()
        page = self._context.new_page()
        captures: list[CaptureRecord] = []
        callback_errors: list[str] = []

        def on_response(response: Any) -> None:
            if self.api_substring not in response.url:
                return
            started = time.monotonic()
            try:
                body = response.body()
                content_type = response.headers.get("content-type", "")
                payload = parse_portal_response_bytes(body, content_type)
                request_headers = {
                    str(k).casefold(): str(v)
                    for k, v in response.request.headers.items()
                }
                authorization = request_headers.get("authorization")
                if authorization and authorization.casefold().startswith("bearer "):
                    self._last_authorization = authorization
                captures.append(
                    CaptureRecord(
                        url=response.url,
                        status=response.status,
                        content_type=content_type,
                        body=body,
                        payload=payload,
                        acquisition="browser_xhr",
                        elapsed_ms=(time.monotonic() - started) * 1000,
                        headers={str(k): str(v) for k, v in response.headers.items()},
                    )
                )
            except Exception as exc:  # preserve diagnostic instead of swallowing
                callback_errors.append(f"{response.url}: {type(exc).__name__}: {exc}")

        page.on("response", on_response)
        try:
            page.goto(detail_url, wait_until="domcontentloaded")
            page.wait_for_timeout(max(0, int(self.settle_seconds * 1000)))
            rendered_html = page.content()
        except Exception as exc:
            raise VbplPortalError(f"Playwright detail fetch failed: {exc}") from exc
        finally:
            page.close()
        if not captures and callback_errors:
            raise VbplPortalError(
                "Browser captured VBPL responses but none were parseable: "
                + " | ".join(callback_errors[:5])
            )
        return rendered_html, captures

    def fetch_binary(
        self,
        url: str,
        *,
        referer: str,
        retries: int = 3,
        backoff_seconds: float = 1.0,
    ) -> HttpResponse:
        """Download an attachment through the authenticated browser context.

        Cookies and any captured Bearer value remain in memory and are never
        written to snapshot metadata or logs.
        """

        self._ensure_started()
        last_error: Exception | None = None
        for attempt in range(1, max(1, retries) + 1):
            started = time.monotonic()
            headers = {"Accept": "*/*", "Referer": referer}
            if self._last_authorization:
                headers["Authorization"] = self._last_authorization
            try:
                response = self._context.request.get(
                    url,
                    headers=headers,
                    timeout=int(self.timeout * 1000),
                    fail_on_status_code=False,
                )
                status = int(response.status)
                body = response.body()
                response_headers = {
                    str(k): str(v) for k, v in response.headers.items()
                }
                if status >= 400:
                    raise VbplPortalError(
                        f"Browser-context attachment HTTP {status} for {url}"
                    )
                return HttpResponse(
                    requested_url=url,
                    final_url=str(response.url),
                    status=status,
                    headers=response_headers,
                    body=body,
                    attempts=attempt,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                )
            except Exception as exc:  # pragma: no cover - live-portal dependent
                last_error = exc
                if attempt < retries:
                    time.sleep(backoff_seconds * (2 ** (attempt - 1)))
        raise VbplPortalError(
            f"Browser-context attachment download failed for {url}: {last_error}"
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_document_number(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.upper().replace("Đ", "D")
    return re.sub(r"[^A-Z0-9]", "", value)


def safe_slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").casefold()


def document_number_slug(document_number: str) -> str:
    value = unicodedata.normalize("NFKD", str(document_number))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("đ", "d").replace("Đ", "D").casefold()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    temp.write_bytes(value)
    os.replace(temp, path)


def atomic_write_text(path: Path, value: str) -> None:
    atomic_write_bytes(path, value.encode("utf-8"))


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
    )


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    value = headers.get("Retry-After") or headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
            return max(0.0, (dt - datetime.now(dt.tzinfo or timezone.utc)).total_seconds())
        except Exception:
            return None


class RetryingHttpClient:
    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.timeout = timeout
        self.retries = max(1, retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.max_bytes = max_bytes

    def fetch(
        self,
        url: str,
        *,
        accept: str = "*/*",
        referer: str | None = None,
    ) -> HttpResponse:
        last_error: Exception | None = None
        started_all = time.monotonic()
        for attempt in range(1, self.retries + 1):
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "Chrome/124 Safari/537.36 vn-labor-law-rag/ingestion-v4"
                ),
                "Accept": accept,
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.5",
            }
            if referer:
                headers["Referer"] = referer
            request = Request(url, headers=headers)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    response_headers = {str(k): str(v) for k, v in response.headers.items()}
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self.max_bytes:
                        raise VbplPortalError(f"Response too large: {url}")
                    body = response.read(self.max_bytes + 1)
                    if len(body) > self.max_bytes:
                        raise VbplPortalError(f"Response too large: {url}")
                    return HttpResponse(
                        requested_url=url,
                        final_url=response.geturl(),
                        status=int(getattr(response, "status", 200)),
                        headers=response_headers,
                        body=body,
                        attempts=attempt,
                        elapsed_ms=(time.monotonic() - started_all) * 1000,
                    )
            except HTTPError as exc:
                response_headers = {str(k): str(v) for k, v in (exc.headers or {}).items()}
                last_error = exc
                retryable = exc.code in RETRY_HTTP_CODES
                if not retryable or attempt == self.retries:
                    break
                delay = _retry_after_seconds(response_headers)
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.retries:
                    break
                delay = None
            if delay is None:
                delay = self.backoff_seconds * (2 ** (attempt - 1))
                delay += random.uniform(0.0, min(0.25, delay * 0.1))
            LOGGER.warning(
                "Transient fetch error; retrying",
                extra={"url": url, "attempt": attempt, "delay": delay},
            )
            time.sleep(delay)
        raise VbplPortalError(
            f"Cannot fetch {url} after {self.retries} attempt(s): {last_error}"
        ) from last_error


def fetch_bytes(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> bytes:
    """Compatibility wrapper used by tests and helper scripts."""
    return RetryingHttpClient(
        timeout=timeout, retries=retries, backoff_seconds=backoff_seconds
    ).fetch(url).body


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(xml_bytes: bytes) -> tuple[str, list[str]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise VbplPortalError(f"Invalid sitemap XML: {exc}") from exc
    kind = _local_name(root.tag)
    if kind not in {"sitemapindex", "urlset"}:
        raise VbplPortalError(f"Unsupported sitemap root: {kind}")
    urls = [
        node.text.strip()
        for node in root.iter()
        if _local_name(node.tag) == "loc" and node.text and node.text.strip()
    ]
    return kind, urls


def collect_sitemap_urls(
    sitemap_url: str,
    *,
    client: RetryingHttpClient | None = None,
    max_sitemaps: int = 200,
) -> list[str]:
    client = client or RetryingHttpClient()
    pending = [sitemap_url]
    visited: set[str] = set()
    detail_urls: list[str] = []
    while pending:
        current = pending.pop(0)
        if current in visited:
            continue
        if len(visited) >= max_sitemaps:
            raise VbplPortalError("Sitemap recursion limit exceeded")
        visited.add(current)
        kind, urls = parse_sitemap(client.fetch(current, accept="application/xml,text/xml,*/*").body)
        if kind == "sitemapindex":
            pending.extend(url for url in urls if url not in visited)
        else:
            detail_urls.extend(urls)
    return list(dict.fromkeys(detail_urls))


def _sitemap_cache_path(output_root: Path, sitemap_url: str) -> Path:
    key = sha256_bytes(sitemap_url.encode("utf-8"))[:16]
    return output_root / "_cache" / f"sitemap-{key}.json.gz"


def load_or_collect_sitemap_urls(
    *,
    output_root: Path,
    sitemap_url: str,
    client: RetryingHttpClient,
    ttl_hours: float = DEFAULT_SITEMAP_CACHE_TTL_HOURS,
    force_refresh: bool = False,
) -> list[str]:
    cache_path = _sitemap_cache_path(output_root, sitemap_url)
    if cache_path.exists() and not force_refresh:
        try:
            with gzip.open(cache_path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            fetched_at = parse_iso_datetime(str(payload["fetched_at"]))
            age = datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc)
            if age.total_seconds() <= max(0.0, ttl_hours) * 3600:
                urls = payload.get("urls")
                if isinstance(urls, list) and all(isinstance(url, str) for url in urls):
                    return urls
        except Exception:
            LOGGER.exception("Ignoring invalid sitemap cache: %s", cache_path)
    urls = collect_sitemap_urls(sitemap_url, client=client)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp = cache_path.with_name(f".{cache_path.name}.tmp-{uuid.uuid4().hex[:8]}")
    with gzip.open(temp, "wt", encoding="utf-8") as stream:
        json.dump(
            {
                "schema_version": "vbpl-sitemap-cache-v1",
                "sitemap_url": sitemap_url,
                "fetched_at": utc_now(),
                "url_count": len(urls),
                "urls_sha256": sha256_bytes("\n".join(urls).encode("utf-8")),
                "urls": urls,
            },
            stream,
            ensure_ascii=False,
        )
    os.replace(temp, cache_path)
    return urls


def score_document_url(url: str, document_number: str) -> int:
    target_slug = document_number_slug(document_number)
    candidate_slug = document_number_slug(urlparse(url).path)
    if not target_slug or not candidate_slug:
        return 0
    if not re.search(rf"(?:^|-){re.escape(target_slug)}(?:-|$)", candidate_slug):
        return 0
    score = 10_000
    if "/van-ban/chi-tiet/" in urlparse(url).path.casefold():
        score += 1_000
    score -= min(len(candidate_slug), 999)
    return score


def resolve_document_url(
    urls: Sequence[str],
    document_number: str,
    *,
    explicit_url: str | None = None,
) -> str:
    if explicit_url:
        parsed = urlparse(explicit_url)
        if parsed.scheme != "https" or parsed.netloc.casefold() not in {
            "vbpl.vn",
            "www.vbpl.vn",
        }:
            raise VbplPortalError("Explicit portal URL must be HTTPS on vbpl.vn")
        return explicit_url
    ranked = sorted(
        ((score_document_url(url, document_number), url) for url in urls),
        reverse=True,
    )
    ranked = [(score, url) for score, url in ranked if score > 0]
    if not ranked:
        raise VbplPortalNotFound(
            f"No exact sitemap URL for {document_number}; pass --portal-url or set portal_url in config"
        )
    top_score = ranked[0][0]
    top = [url for score, url in ranked if score == top_score]
    if len(top) != 1:
        raise VbplPortalAmbiguous(
            f"Ambiguous exact sitemap matches for {document_number}: {top[:5]}"
        )
    return top[0]


def xml_element_to_data(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return (element.text or "").strip()
    result: dict[str, Any] = {}
    for child in children:
        key = _local_name(child.tag)
        value = xml_element_to_data(child)
        if key in result:
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(value)
        else:
            result[key] = value
    text = (element.text or "").strip()
    if text:
        result.setdefault("_text", text)
    return result


def parse_portal_response_bytes(value: bytes, content_type: str = "") -> Any:
    if not value:
        raise VbplPortalError("Empty gateway response")
    text = value.decode("utf-8-sig", errors="replace").strip()
    errors: list[str] = []
    prefer_json = "json" in content_type.casefold() or text.startswith(("{", "["))
    prefer_xml = "xml" in content_type.casefold() or text.startswith("<")
    attempts = ["json", "xml"] if prefer_json else ["xml", "json"] if prefer_xml else ["json", "xml"]
    for kind in attempts:
        try:
            if kind == "json":
                return json.loads(text)
            root = ET.fromstring(text)
            return {_local_name(root.tag): xml_element_to_data(root)}
        except Exception as exc:
            errors.append(f"{kind}={exc}")
    raise VbplPortalError("Unsupported gateway response: " + "; ".join(errors))


def extract_item_id_from_detail_url(detail_url: str) -> str | None:
    parsed = urlparse(detail_url)
    query = parse_qs(parsed.query)
    for key in ("ItemID", "itemId", "itemid", "id"):
        values = query.get(key)
        if values and str(values[0]).isdigit():
            return str(values[0])
    match = re.search(r"--([A-Za-z0-9_]+)(?:[/?#]|$)", detail_url)
    return match.group(1) if match else None


def detail_url_from_item_id(item_id: str | int) -> str:
    value = str(item_id).strip()
    if not value.isdigit():
        raise VbplPortalError(f"Invalid VBPL item_id: {item_id!r}")
    return f"https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID={value}"


def gateway_url_from_detail_url(detail_url: str) -> str | None:
    item_id = extract_item_id_from_detail_url(detail_url)
    if not item_id or not item_id.isdigit():
        return None
    return DEFAULT_GATEWAY_TEMPLATE.format(item_id=item_id)


def walk_json(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, Mapping):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def first_value(node: Mapping[str, Any], keys: Iterable[str]) -> Any:
    key_map = {str(key).casefold(): key for key in node}
    for candidate in keys:
        actual = key_map.get(candidate.casefold())
        if actual is not None:
            value = node[actual]
            if value not in (None, "", [], {}):
                return value
    return None


DOCUMENT_NUMBER_KEYS = ("soHieu", "soHieuVanBan", "documentNumber", "docNum", "kyHieu")
TITLE_KEYS = ("tieuDe", "tenVanBan", "docName", "documentName", "title", "name", "trichYeu")
BODY_KEYS = (
    "content",
    "noiDung",
    "toanVan",
    "noiDungVanBan",
    "body",
    "htmlContent",
    "bodyHtml",
    "fullText",
    "toanVanHtml",
)
APPENDIX_KEYS = ("phuLuc", "noiDungPhuLuc", "appendix", "appendixHtml")
ISSUED_KEYS = ("ngayBanHanh", "issueDate", "ngayKy", "issuedAt")
EFFECTIVE_FROM_KEYS = ("ngayHieuLuc", "effFrom", "effectiveDate", "effectiveFrom")
EFFECTIVE_TO_KEYS = ("ngayHetHieuLuc", "effTo", "expiryDate", "effectiveTo")
STATUS_KEYS = ("tinhTrangHieuLuc", "effStatus", "statusName", "legalStatus", "status")
AUTHORITY_KEYS = (
    "coQuanBanHanh",
    "issueOrg",
    "issueOrgName",
    "orgName",
    "agencyName",
    "issuingAuthority",
    "coQuan",
)
ITEM_ID_KEYS = ("id", "itemId", "documentId", "docId")
TYPE_KEYS = ("typeVb", "docType", "loaiVanBanId", "documentTypeId", "typeId")
ATTACHMENT_COLLECTION_KEYS = (
    "tepDinhKem",
    "fileDinhKem",
    "danhSachFile",
    "files",
    "attachments",
    "tepTin",
    "fileList",
)
ATTACHMENT_URL_KEYS = (
    "filePath",
    "url",
    "fileUrl",
    "downloadUrl",
    "duongDan",
    "path",
    "link",
    "documentContentFileUrl",
    "documentContentFileDocUrl",
)
ATTACHMENT_NAME_KEYS = (
    "name",
    "fileName",
    "tenFile",
    "title",
    "originalName",
    "documentContentFileName",
    "documentContentFileDocName",
)
PRIMARY_FILE_NAME_KEYS = (
    "documentContentFileName",
    "documentContentFileDocName",
    "documentFileName",
)
PRIMARY_FILE_URL_KEYS = (
    "documentContentFileUrl",
    "documentContentFileDocUrl",
    "documentFileUrl",
)


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return html.unescape(value).strip()
    if isinstance(value, Mapping):
        nested = first_value(
            value,
            (
                "name",
                "title",
                "label",
                "value",
                "ten",
                "moTa",
                "orgName",
                "docName",
                "statusName",
            ),
        )
        return _to_text(nested) if nested is not None else ""
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    return ""


def recursive_first_value(value: Any, keys: Iterable[str]) -> Any:
    for node in walk_json(value):
        if isinstance(node, Mapping):
            found = first_value(node, keys)
            if found not in (None, "", [], {}):
                return found
    return None


def largest_recursive_text(payloads: Sequence[Any], keys: Iterable[str]) -> str:
    values: list[str] = []
    for payload in payloads:
        for node in walk_json(payload):
            if isinstance(node, Mapping):
                text = _to_text(first_value(node, keys))
                if text:
                    values.append(text)
    return max(values, key=len, default="")


def _date_text(value: Any) -> str | None:
    text = _to_text(value)
    if not text:
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else text


def _attachment_record(
    *, name: str, url: str | None, base_url: str, source_field: str
) -> dict[str, Any] | None:
    resolved_url: str | None = None
    if url:
        candidate = urljoin(base_url, url)
        if urlparse(candidate).scheme in {"http", "https"}:
            resolved_url = candidate
    clean_name = Path(name.replace("\\", "/")).name.strip() if name else ""
    if not clean_name and resolved_url:
        clean_name = Path(urlparse(resolved_url).path).name
    if not clean_name and not resolved_url:
        return None
    return {
        "name": clean_name or "attachment",
        "url": resolved_url,
        "resolution_status": "resolved" if resolved_url else "unresolved",
        "source_field": source_field,
    }


def extract_attachment_inventory(node: Mapping[str, Any], base_url: str) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for current in walk_json(node):
        if not isinstance(current, Mapping):
            continue
        raw_collection = first_value(current, ATTACHMENT_COLLECTION_KEYS)
        if raw_collection is not None:
            candidates = raw_collection if isinstance(raw_collection, list) else [raw_collection]
            for candidate in candidates:
                if isinstance(candidate, str):
                    record = _attachment_record(
                        name=Path(urlparse(candidate).path).name,
                        url=candidate,
                        base_url=base_url,
                        source_field="attachment_collection",
                    )
                elif isinstance(candidate, Mapping):
                    record = _attachment_record(
                        name=_to_text(first_value(candidate, ATTACHMENT_NAME_KEYS)),
                        url=_to_text(first_value(candidate, ATTACHMENT_URL_KEYS)) or None,
                        base_url=base_url,
                        source_field="attachment_collection",
                    )
                else:
                    record = None
                if record:
                    records.append(record)
        primary_name = _to_text(first_value(current, PRIMARY_FILE_NAME_KEYS))
        primary_url = _to_text(first_value(current, PRIMARY_FILE_URL_KEYS))
        if primary_name or primary_url:
            record = _attachment_record(
                name=primary_name,
                url=primary_url or None,
                base_url=base_url,
                source_field="document_content_file",
            )
            if record:
                records.append(record)
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record.get("url") or f"name:{record['name'].casefold()}"
        unique[str(key)] = record
    return tuple(unique.values())


def merge_configured_attachments(
    document: PortalDocument,
    configured: Sequence[Mapping[str, Any]],
    *,
    detail_url: str,
) -> PortalDocument:
    """Merge explicitly approved official attachment sources into a document.

    This is intentionally config-driven rather than an automatic source fallback.
    Every configured URL is persisted in the snapshot inventory together with its
    provider and source page so release review can audit the provenance.
    """

    # Once an explicit official source is configured, filename-only gateway hints
    # are superseded by that auditable source. Resolved gateway URLs are retained.
    records = [
        dict(item)
        for item in document.attachment_inventory
        if item.get("url") or not configured
    ]
    for item in configured:
        if not isinstance(item, Mapping):
            raise VbplPortalError("official_attachments entries must be objects")
        record = _attachment_record(
            name=_to_text(item.get("name")),
            url=_to_text(item.get("url")) or None,
            base_url=detail_url,
            source_field="configured_official_attachment",
        )
        if record is None or not record.get("url"):
            raise VbplPortalError(
                f"Configured official attachment has no resolvable URL: {item!r}"
            )
        for key in ("provider", "source_page_url", "role", "label"):
            value = item.get(key)
            if value not in (None, ""):
                record[key] = str(value)
        records.append(record)

    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record.get("url") or f"name:{str(record.get('name')).casefold()}")
        # Explicit configured sources override unresolved gateway filename-only rows
        # with the same name, while still preserving unrelated attachments.
        if record.get("url"):
            name_key = f"name:{str(record.get('name')).casefold()}"
            unique.pop(name_key, None)
        unique[key] = record
    return replace(document, attachment_inventory=tuple(unique.values()))


def build_ingestion_contract(
    *,
    document_number: str,
    item_id: str | int | None,
    expected_articles: int | None,
    attachment_policy: str,
    configured_attachments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    attachments = []
    for item in configured_attachments:
        attachments.append(
            {
                "name": str(item.get("name") or ""),
                "url": str(item.get("url") or ""),
                "provider": str(item.get("provider") or ""),
                "source_page_url": str(item.get("source_page_url") or ""),
                "role": str(item.get("role") or ""),
            }
        )
    contract = {
        "schema_version": INGESTION_CONTRACT_SCHEMA,
        "document_number": document_number,
        "item_id": str(item_id) if item_id not in (None, "") else None,
        "expected_articles": expected_articles,
        "attachment_policy": attachment_policy,
        "configured_attachments": sorted(
            attachments, key=lambda item: (item["url"], item["name"])
        ),
        "snapshot_schema": SNAPSHOT_SCHEMA,
    }
    encoded = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "contract": contract,
        "sha256": sha256_bytes(encoded),
    }


def snapshot_contract_matches(
    snapshot: Path, expected_contract: Mapping[str, Any]
) -> bool:
    try:
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    expected_hash = str(expected_contract.get("sha256") or "")
    return bool(expected_hash) and manifest.get("ingestion_contract_sha256") == expected_hash


def extract_document_from_payloads(
    payloads: Sequence[Any], document_number: str, *, detail_url: str
) -> PortalDocument:
    target = normalize_document_number(document_number)
    candidates: list[tuple[int, Mapping[str, Any]]] = []
    for payload in payloads:
        for node in walk_json(payload):
            if not isinstance(node, Mapping):
                continue
            number = _to_text(first_value(node, DOCUMENT_NUMBER_KEYS))
            body = _to_text(recursive_first_value(node, BODY_KEYS))
            score = 0
            if number and normalize_document_number(number) == target:
                score += 10_000
            if body and re.search(r"(?i)\bĐiều\s+\d+", html.unescape(body)):
                score += min(1_000, len(body) // 300)
            if first_value(node, TITLE_KEYS):
                score += 20
            if score:
                candidates.append((score, node))
    if not candidates:
        raise VbplPortalError(
            f"Captured API data did not contain a document record for {document_number}"
        )
    candidates.sort(key=lambda item: item[0], reverse=True)
    node = candidates[0][1]
    actual_number = _to_text(first_value(node, DOCUMENT_NUMBER_KEYS))
    if normalize_document_number(actual_number) != target:
        raise VbplPortalError(
            f"Best API record is not an exact document-number match: {actual_number!r}"
        )
    full_text = _to_text(recursive_first_value(node, BODY_KEYS))
    if not full_text:
        full_text = largest_recursive_text(payloads, BODY_KEYS)
    if not full_text:
        raise VbplPortalError("Exact API record has no full text")
    if "<" not in full_text or ">" not in full_text:
        full_text = "".join(
            f"<p>{html.escape(line)}</p>"
            for line in full_text.splitlines()
            if line.strip()
        )
    return PortalDocument(
        document_number=actual_number,
        title=_to_text(first_value(node, TITLE_KEYS)) or document_number,
        full_text_html=full_text,
        issued_at=_date_text(first_value(node, ISSUED_KEYS)),
        effective_from=_date_text(first_value(node, EFFECTIVE_FROM_KEYS)),
        effective_to=_date_text(first_value(node, EFFECTIVE_TO_KEYS)),
        legal_status=_to_text(first_value(node, STATUS_KEYS)) or None,
        issuing_authority=_to_text(first_value(node, AUTHORITY_KEYS)) or None,
        item_id=first_value(node, ITEM_ID_KEYS),
        type_vb=first_value(node, TYPE_KEYS),
        appendix_html=_to_text(recursive_first_value(node, APPENDIX_KEYS)),
        attachment_inventory=extract_attachment_inventory(node, detail_url),
    )


def html_to_text(value: str) -> str:
    try:
        root = lxml_html.fragment_fromstring(value, create_parent="div")
    except Exception as exc:
        raise VbplPortalError(f"Invalid full-text HTML: {exc}") from exc
    for br in root.xpath(".//br"):
        br.tail = "\n" + (br.tail or "")
    text = "\n".join(part.strip() for part in root.itertext() if part.strip())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def detect_article_numbers(value: str) -> list[int]:
    text = html_to_text(value)
    return [int(number) for number in re.findall(r"(?mi)^\s*Điều\s+(\d+)\b", text)]


def article_sequence_report(value: str, expected_articles: int | None) -> dict[str, Any]:
    numbers = detect_article_numbers(value)
    # Legal documents may repeat article headings after the main body, for example
    # inside appendices or quoted/amended provisions.  The fail-closed contract is
    # therefore based on the first occurrence of every article number: the main
    # sequence must still be exactly 1..N, with no missing or unexpected article.
    unique = list(dict.fromkeys(numbers))
    duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
    expected = list(range(1, expected_articles + 1)) if expected_articles else list(range(1, max(unique, default=0) + 1))
    sequence_valid = bool(unique) and unique == expected
    return {
        "detected_count": len(numbers),
        "unique_count": len(unique),
        "first": unique[0] if unique else None,
        "last": unique[-1] if unique else None,
        "duplicates": duplicates,
        "missing": sorted(set(expected) - set(unique)),
        "unexpected": sorted(set(unique) - set(expected)) if expected else [],
        "ordered": unique == sorted(unique),
        "valid": sequence_valid,
    }


def validate_attachment_signature(filename: str, value: bytes) -> str:
    if not value:
        return "invalid_empty"
    prefix = value[:512].lstrip().lower()
    if prefix.startswith((b"<!doctype html", b"<html")):
        return "invalid_html_error_page"
    lowered = filename.casefold()
    if lowered.endswith(".pdf"):
        return "valid" if value.startswith(b"%PDF-") else "invalid_signature"
    if lowered.endswith((".docx", ".xlsx", ".zip")):
        return "valid" if value.startswith(b"PK\x03\x04") else "invalid_signature"
    if lowered.endswith(".doc"):
        return "valid" if value.startswith(b"\xd0\xcf\x11\xe0") else "not_checked"
    return "not_checked"


def _safe_filename(value: str, fallback: str) -> str:
    value = Path(value.replace("\\", "/")).name
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", value).strip(" .")
    return value or fallback


def _response_suffix(capture: CaptureRecord) -> str:
    content_type = capture.content_type.casefold()
    stripped = capture.body.lstrip()
    if "json" in content_type or stripped.startswith((b"{", b"[")):
        return ".json"
    if "xml" in content_type or stripped.startswith(b"<"):
        return ".xml"
    return ".bin"


def write_capture_artifacts(snapshot: Path, captures: Sequence[CaptureRecord]) -> list[dict[str, Any]]:
    root = snapshot / "portal" / "responses"
    root.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for index, capture in enumerate(captures, 1):
        prefix = f"{index:03d}"
        raw_path = root / f"{prefix}.response{_response_suffix(capture)}"
        raw_path.write_bytes(capture.body)
        parsed_path = root / f"{prefix}.parsed.json"
        write_json(parsed_path, capture.payload)
        summary = {
            "url": capture.url,
            "status": capture.status,
            "content_type": capture.content_type,
            "acquisition": capture.acquisition,
            "attempts": capture.attempts,
            "elapsed_ms": round(capture.elapsed_ms, 3),
            "headers": capture.headers,
            "raw_path": raw_path.relative_to(snapshot).as_posix(),
            "raw_sha256": sha256_file(raw_path),
            "raw_size_bytes": raw_path.stat().st_size,
            "parsed_path": parsed_path.relative_to(snapshot).as_posix(),
        }
        summaries.append(summary)
    write_json(snapshot / "portal" / "api_responses.json", summaries)
    return summaries


def write_checksums(snapshot: Path) -> None:
    paths = sorted(
        path
        for path in snapshot.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    lines = "".join(
        f"{sha256_file(path)}  {path.relative_to(snapshot).as_posix()}\n"
        for path in paths
    )
    atomic_write_text(snapshot / "SHA256SUMS.txt", lines)


def parse_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise VbplPortalError(f"Invalid checksum line in {path}: {line!r}")
        digest, relative = match.groups()
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise VbplPortalError(f"Unsafe checksum path: {relative}")
        checksums[relative] = digest
    return checksums


def verify_snapshot(
    snapshot: Path, *, expected_document_number: str | None = None
) -> dict[str, Any]:
    required = {
        "manifest.json",
        "SHA256SUMS.txt",
        "full_text.html",
        "full_text.txt",
        "portal/api_responses.json",
    }
    missing = sorted(name for name in required if not (snapshot / name).is_file())
    if missing:
        raise VbplPortalError(f"Snapshot missing required files: {missing}")
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA:
        raise VbplPortalError(
            f"Unsupported snapshot schema: {manifest.get('schema_version')!r}"
        )
    actual_number = str((manifest.get("document") or {}).get("document_number") or "")
    if expected_document_number and normalize_document_number(actual_number) != normalize_document_number(expected_document_number):
        raise VbplPortalError(
            f"Snapshot identity mismatch: {actual_number!r} != {expected_document_number!r}"
        )
    gates = manifest.get("gates") or {}
    failed_gates = sorted(name for name, value in gates.items() if value is not True)
    if not gates or failed_gates:
        raise VbplPortalError(f"Snapshot has failed gates: {failed_gates or ['missing_gates']}")
    expected_checksums = parse_checksums(snapshot / "SHA256SUMS.txt")
    missing_checksum_entries = sorted(required - {"SHA256SUMS.txt"} - set(expected_checksums))
    if missing_checksum_entries:
        raise VbplPortalError(
            f"Critical files absent from checksum manifest: {missing_checksum_entries}"
        )
    mismatches: list[str] = []
    for relative, expected_hash in expected_checksums.items():
        path = snapshot / relative
        if not path.is_file() or sha256_file(path) != expected_hash:
            mismatches.append(relative)
    if mismatches:
        raise VbplPortalError(f"Snapshot checksum mismatch: {mismatches}")
    contract = manifest.get("ingestion_contract")
    contract_hash = manifest.get("ingestion_contract_sha256")
    if contract is not None or contract_hash is not None:
        if not isinstance(contract, Mapping) or not contract_hash:
            raise VbplPortalError("Snapshot ingestion contract is incomplete")
        encoded_contract = json.dumps(
            contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if sha256_bytes(encoded_contract) != contract_hash:
            raise VbplPortalError("Snapshot ingestion contract hash mismatch")
    content_hash = sha256_file(snapshot / "full_text.html")
    manifest_hash = ((manifest.get("content_hashes") or {}).get("full_text_html_sha256"))
    if content_hash != manifest_hash:
        raise VbplPortalError("full_text.html hash does not match manifest")
    return {
        "snapshot": str(snapshot),
        "document_number": actual_number,
        "files_verified": len(expected_checksums),
        "content_sha256": content_hash,
        "valid": True,
    }


def latest_valid_snapshot(
    output_root: Path,
    document_number: str,
    *,
    expected_contract: Mapping[str, Any] | None = None,
) -> Path | None:
    root = output_root / safe_slug(document_number)
    if not root.exists():
        return None
    candidates = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir()
            and not path.name.startswith(".tmp-")
            and path.name != ".ingest.lock"
        ),
        reverse=True,
    )
    for candidate in candidates:
        try:
            verify_snapshot(candidate, expected_document_number=document_number)
            if expected_contract is not None and not snapshot_contract_matches(
                candidate, expected_contract
            ):
                LOGGER.info(
                    "Ignoring snapshot with incompatible ingestion contract: %s",
                    candidate,
                )
                continue
            return candidate
        except Exception:
            LOGGER.warning("Ignoring invalid snapshot during resume: %s", candidate)
    return None


@contextmanager
def document_lock(
    output_root: Path,
    document_number: str,
    *,
    stale_seconds: float = DEFAULT_LOCK_STALE_SECONDS,
) -> Iterator[None]:
    root = output_root / safe_slug(document_number)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".ingest.lock"
    if lock.exists():
        try:
            owner = json.loads((lock / "owner.json").read_text(encoding="utf-8"))
            created = parse_iso_datetime(str(owner["created_at"]))
            age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
        except Exception:
            try:
                age = max(0.0, time.time() - lock.stat().st_mtime)
            except OSError:
                age = 0.0
        if age > stale_seconds:
            shutil.rmtree(lock, ignore_errors=True)
        else:
            raise VbplPortalLockError(
                f"Another ingestion process holds {lock}. Remove it only if the process is no longer running."
            )
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise VbplPortalLockError(f"Concurrent ingestion lock: {lock}") from exc
    write_json(
        lock / "owner.json",
        {
            "pid": os.getpid(),
            "created_at": utc_now(),
            "document_number": document_number,
        },
    )
    try:
        yield
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def _attachment_policy_satisfied(
    policy: str,
    inventory: Sequence[Mapping[str, Any]],
    manifest: Sequence[Mapping[str, Any]],
) -> bool:
    if policy not in ATTACHMENT_POLICIES:
        raise VbplPortalError(f"Unknown attachment policy: {policy}")
    if policy in {"ignore", "best_effort"}:
        return True
    if policy == "required" and not inventory:
        return False
    if not inventory:
        return True
    by_key = {
        str(item.get("url") or f"name:{str(item.get('name')).casefold()}"): item
        for item in manifest
    }
    for item in inventory:
        key = str(item.get("url") or f"name:{str(item.get('name')).casefold()}")
        saved = by_key.get(key) or {}
        if not item.get("url"):
            return False
        if saved.get("has_bytes") is not True:
            return False
        if saved.get("signature_status") not in {"valid", "not_checked"}:
            return False
    return True


def write_snapshot(
    *,
    output_root: Path,
    document: PortalDocument,
    expected_document_number: str | None = None,
    detail_url: str,
    sitemap_url: str,
    captures: Sequence[CaptureRecord],
    rendered_html: str,
    expected_articles: int | None,
    attachment_responses: Mapping[str, HttpResponse] | None = None,
    attachment_policy: str = "best_effort",
    ingestion_contract: Mapping[str, Any] | None = None,
    operations: Sequence[str] = (),
    retrieved_at: str | None = None,
    force_snapshot: bool = False,
) -> dict[str, Any]:
    if not captures:
        raise VbplPortalError("Cannot write snapshot without a captured official response")
    document_root = output_root / safe_slug(document.document_number)
    document_root.mkdir(parents=True, exist_ok=True)
    full_text_hash = sha256_bytes(document.full_text_html.encode("utf-8"))
    if not force_snapshot:
        previous = latest_valid_snapshot(
            output_root,
            document.document_number,
            expected_contract=ingestion_contract,
        )
        if previous:
            previous_manifest = json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
            previous_hash = (previous_manifest.get("content_hashes") or {}).get("full_text_html_sha256")
            if previous_hash == full_text_hash:
                return {
                    "status": "unchanged",
                    "snapshot_dir": str(previous),
                    "manifest": previous_manifest,
                }
    retrieved_at = retrieved_at or utc_now()
    timestamp = parse_iso_datetime(retrieved_at).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_snapshot = document_root / f"{timestamp}-{full_text_hash[:8]}"
    if final_snapshot.exists():
        raise VbplPortalError(f"Snapshot already exists: {final_snapshot}")
    snapshot = document_root / f".tmp-{timestamp}-{uuid.uuid4().hex[:8]}"
    (snapshot / "portal").mkdir(parents=True, exist_ok=False)
    try:
        write_json(
            snapshot / "portal" / "sitemap_entry.json",
            {"sitemap_url": sitemap_url, "detail_url": detail_url},
        )
        atomic_write_text(snapshot / "portal" / "detail_page.html", rendered_html)
        capture_summaries = write_capture_artifacts(snapshot, captures)
        atomic_write_text(snapshot / "full_text.html", document.full_text_html)
        atomic_write_text(snapshot / "full_text.txt", html_to_text(document.full_text_html) + "\n")
        if document.appendix_html:
            atomic_write_text(snapshot / "appendix.html", document.appendix_html)

        inventory = [dict(item) for item in document.attachment_inventory]
        write_json(snapshot / "portal" / "attachment_inventory.json", inventory)
        responses = dict(attachment_responses or {})
        attachments_manifest: list[dict[str, Any]] = []
        if inventory:
            (snapshot / "attachments").mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(inventory, 1):
            url = item.get("url")
            name = _safe_filename(str(item.get("name") or ""), f"attachment_{index}")
            response = responses.get(str(url)) if url else None
            record = dict(item)
            record.update({"name": name, "has_bytes": response is not None})
            if response is not None:
                path = snapshot / "attachments" / name
                suffix = 1
                while path.exists():
                    path = snapshot / "attachments" / f"{path.stem}_{suffix}{path.suffix}"
                    suffix += 1
                path.write_bytes(response.body)
                record.update(
                    {
                        "saved_path": path.relative_to(snapshot).as_posix(),
                        "sha256": sha256_bytes(response.body),
                        "size_bytes": len(response.body),
                        "signature_status": validate_attachment_signature(name, response.body),
                        "http_status": response.status,
                        "attempts": response.attempts,
                        "elapsed_ms": round(response.elapsed_ms, 3),
                    }
                )
            attachments_manifest.append(record)

        article_report = article_sequence_report(document.full_text_html, expected_articles)
        detail_item_id = extract_item_id_from_detail_url(detail_url)
        item_id_match = (
            not detail_item_id
            or document.item_id is None
            or str(document.item_id) == str(detail_item_id)
        )
        text_length = len(html_to_text(document.full_text_html))
        attachment_policy_ok = _attachment_policy_satisfied(
            attachment_policy, inventory, attachments_manifest
        )
        warnings: list[str] = []
        unresolved = [item for item in inventory if not item.get("url")]
        failed_downloads = [
            item for item in attachments_manifest if item.get("url") and not item.get("has_bytes")
        ]
        if unresolved:
            warnings.append(f"{len(unresolved)} attachment metadata record(s) have no resolvable URL")
        if failed_downloads:
            warnings.append(f"{len(failed_downloads)} attachment download(s) failed or were skipped")
        if not rendered_html:
            warnings.append("Detail-page HTML was unavailable; gateway response is the primary source")

        gates = {
            "exact_document_number": bool(document.document_number) and (
                expected_document_number is None
                or normalize_document_number(document.document_number)
                == normalize_document_number(expected_document_number)
            ),
            "detail_item_id_consistent": item_id_match,
            "full_text_nonempty": text_length >= 100,
            "article_sequence_valid": article_report["valid"],
            "identity_metadata_complete": bool(document.document_number and document.title and document.item_id),
            "legal_effect_metadata_present": bool(
                document.issued_at or document.effective_from or document.legal_status
            ),
            "official_response_persisted": bool(capture_summaries),
            "attachment_policy_satisfied": attachment_policy_ok,
        }
        acquisition_modes = sorted({capture.acquisition for capture in captures})
        manifest = {
            "schema_version": SNAPSHOT_SCHEMA,
            "review_status": "staged_unapproved",
            "ingestion_contract": (
                dict(ingestion_contract.get("contract") or {})
                if ingestion_contract
                else None
            ),
            "ingestion_contract_sha256": (
                ingestion_contract.get("sha256") if ingestion_contract else None
            ),
            "retrieved_at": retrieved_at,
            "source": {
                "provider": "Cơ sở dữ liệu quốc gia về văn bản pháp luật",
                "website": DEFAULT_WEBSITE,
                "acquisition_method": "vbpl_portal",
                "sitemap_url": sitemap_url,
                "detail_url": detail_url,
                "gateway_url": gateway_url_from_detail_url(detail_url),
                "operations": list(dict.fromkeys(operations)),
                "acquisition_modes": acquisition_modes,
                "primary_response_path": capture_summaries[0]["raw_path"],
                "response_count": len(capture_summaries),
            },
            "document": {
                "item_id": document.item_id,
                "type_vb": document.type_vb,
                "document_number": document.document_number,
                "title": document.title,
                "issued_at": document.issued_at,
                "effective_from": document.effective_from,
                "effective_to": document.effective_to,
                "legal_status": document.legal_status,
                "issuing_authority": document.issuing_authority,
                "relations": {},
            },
            "article_validation": article_report,
            "attachment_policy": attachment_policy,
            "attachment_inventory": {
                "count": len(inventory),
                "resolved_count": sum(bool(item.get("url")) for item in inventory),
                "unresolved_count": len(unresolved),
                "downloaded_count": sum(bool(item.get("has_bytes")) for item in attachments_manifest),
            },
            "attachments": attachments_manifest,
            "appendix_present": bool(document.appendix_html),
            "content_hashes": {
                "full_text_html_sha256": full_text_hash,
                "full_text_text_sha256": sha256_bytes(
                    (html_to_text(document.full_text_html) + "\n").encode("utf-8")
                ),
                "appendix_html_sha256": (
                    sha256_bytes(document.appendix_html.encode("utf-8"))
                    if document.appendix_html
                    else None
                ),
                "primary_response_sha256": capture_summaries[0]["raw_sha256"],
            },
            "warnings": warnings,
            "gates": gates,
        }
        write_json(snapshot / "manifest.json", manifest)
        write_checksums(snapshot)
        failed = [name for name, passed in gates.items() if passed is not True]
        if failed:
            raise VbplPortalError(f"Snapshot rejected by fail-closed gates: {failed}")
        verify_snapshot(snapshot, expected_document_number=document.document_number)
        os.replace(snapshot, final_snapshot)
        return {"status": "created", "snapshot_dir": str(final_snapshot), "manifest": manifest}
    except Exception:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise


def write_failure_diagnostics(
    *,
    output_root: Path,
    document_number: str,
    detail_url: str,
    rendered_html: str,
    captures: Sequence[CaptureRecord],
    error: Exception,
) -> Path:
    root = (
        output_root
        / "_diagnostics"
        / safe_slug(document_number)
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(root / "detail_page.html", rendered_html)
    summaries = write_capture_artifacts(root, captures) if captures else []
    write_json(
        root / "error.json",
        {
            "document_number": document_number,
            "detail_url": detail_url,
            "error_type": type(error).__name__,
            "error": str(error),
            "captured_response_count": len(summaries),
            "created_at": utc_now(),
        },
    )
    return root


def fetch_gateway_payload_direct(
    detail_url: str, *, client: RetryingHttpClient
) -> CaptureRecord | None:
    gateway_url = gateway_url_from_detail_url(detail_url)
    if not gateway_url:
        return None
    try:
        response = client.fetch(
            gateway_url,
            accept="application/json,application/xml,text/xml,*/*",
            referer=detail_url,
        )
        content_type = response.headers.get("Content-Type", response.headers.get("content-type", ""))
        payload = parse_portal_response_bytes(response.body, content_type)
        return CaptureRecord(
            url=response.final_url,
            status=response.status,
            content_type=content_type,
            body=response.body,
            payload=payload,
            acquisition="direct_gateway",
            attempts=response.attempts,
            elapsed_ms=response.elapsed_ms,
            headers=response.headers,
        )
    except VbplPortalError as exc:
        LOGGER.info("Direct gateway unavailable; browser fallback may be used: %s", exc)
        return None


def _download_attachments(
    document: PortalDocument,
    *,
    client: RetryingHttpClient,
    detail_url: str,
    policy: str,
    browser_session: BrowserSession | None = None,
) -> tuple[dict[str, HttpResponse], list[str]]:
    responses: dict[str, HttpResponse] = {}
    errors: list[str] = []
    if policy == "ignore":
        return responses, errors
    for item in document.attachment_inventory:
        url = item.get("url")
        if not url:
            continue
        try:
            responses[str(url)] = client.fetch(str(url), referer=detail_url)
            continue
        except VbplPortalError as direct_error:
            if browser_session is not None and getattr(
                browser_session, "is_started", True
            ):
                try:
                    responses[str(url)] = browser_session.fetch_binary(
                        str(url), referer=detail_url
                    )
                    continue
                except VbplPortalError as browser_error:
                    errors.append(
                        f"{url}: direct={direct_error}; browser={browser_error}"
                    )
            else:
                errors.append(f"{url}: {direct_error}")
            if policy in {"required", "required_if_listed"}:
                break
    return responses, errors


def fetch_one(
    *,
    document_number: str,
    output_root: Path,
    expected_articles: int | None,
    sitemap_url: str,
    portal_url: str | None,
    item_id: str | int | None,
    api_substring: str,
    headed: bool,
    timeout: float,
    settle_seconds: float,
    attachment_policy: str,
    configured_attachments: Sequence[Mapping[str, Any]] = (),
    sitemap_urls: Sequence[str] | None = None,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    browser_session: BrowserSession | None = None,
    resume: bool = False,
    force_snapshot: bool = False,
) -> dict[str, Any]:
    client = RetryingHttpClient(
        timeout=timeout, retries=retries, backoff_seconds=backoff_seconds
    )
    if attachment_policy not in ATTACHMENT_POLICIES:
        raise VbplPortalError(f"Invalid attachment policy: {attachment_policy}")
    ingestion_contract = build_ingestion_contract(
        document_number=document_number,
        item_id=item_id,
        expected_articles=expected_articles,
        attachment_policy=attachment_policy,
        configured_attachments=configured_attachments,
    )
    if resume and not force_snapshot:
        existing = latest_valid_snapshot(
            output_root,
            document_number,
            expected_contract=ingestion_contract,
        )
        if existing is not None:
            return {
                "status": "skipped_valid",
                "snapshot_dir": str(existing),
                "verification": verify_snapshot(
                    existing, expected_document_number=document_number
                ),
            }
    with document_lock(output_root, document_number):
        operations: list[str] = []
        urls = [] if (portal_url or item_id is not None) else list(
            sitemap_urls or collect_sitemap_urls(sitemap_url, client=client)
        )
        if item_id is not None:
            detail_url = detail_url_from_item_id(item_id)
            operations.append("ResolveRegistryItemId")
        else:
            detail_url = resolve_document_url(
                urls, document_number, explicit_url=portal_url
            )
            operations.append(
                "ResolveExplicitUrl" if portal_url else "ResolveSitemapExact"
            )
        captures: list[CaptureRecord] = []
        rendered_html = ""
        owned_browser: BrowserSession | None = None
        direct = fetch_gateway_payload_direct(detail_url, client=client)
        if direct is not None:
            captures.append(direct)
            operations.append("FetchDirectGateway")
            try:
                detail = client.fetch(detail_url, accept="text/html,*/*")
                rendered_html = detail.body.decode("utf-8-sig", errors="replace")
                operations.append("FetchDetailHtml")
            except VbplPortalError:
                LOGGER.warning("Detail HTML unavailable; continuing with official gateway payload")
        else:
            operations.extend(["OpenBrowserDetail", "CaptureBrowserApi"])
            if browser_session is not None:
                rendered_html, captures = browser_session.fetch_detail(detail_url)
            else:
                owned_browser = BrowserSession(
                    headed=headed,
                    timeout=timeout,
                    settle_seconds=settle_seconds,
                    api_substring=api_substring,
                )
                try:
                    rendered_html, captures = owned_browser.fetch_detail(detail_url)
                except Exception:
                    owned_browser.close()
                    raise
        payloads = [capture.payload for capture in captures]
        try:
            document = extract_document_from_payloads(
                payloads, document_number, detail_url=detail_url
            )
            detail_item_id = extract_item_id_from_detail_url(detail_url)
            if detail_item_id and document.item_id is not None and str(document.item_id) != str(detail_item_id):
                raise VbplPortalError(
                    f"Gateway item id {document.item_id!r} does not match detail URL id {detail_item_id!r}"
                )
            strict_attachments = attachment_policy in {"required", "required_if_listed"}
            if (
                direct is not None
                and strict_attachments
                and document.attachment_inventory
            ):
                operations.extend(
                    ["OpenBrowserForAttachmentDiscovery", "CaptureBrowserApi"]
                )
                if browser_session is not None:
                    browser_html, browser_captures = browser_session.fetch_detail(
                        detail_url
                    )
                else:
                    if owned_browser is None:
                        owned_browser = BrowserSession(
                            headed=headed,
                            timeout=timeout,
                            settle_seconds=settle_seconds,
                            api_substring=api_substring,
                        )
                    browser_html, browser_captures = owned_browser.fetch_detail(
                        detail_url
                    )
                if browser_html:
                    rendered_html = browser_html
                captures.extend(browser_captures)
                payloads = [capture.payload for capture in captures]
                document = extract_document_from_payloads(
                    payloads, document_number, detail_url=detail_url
                )

            if configured_attachments:
                document = merge_configured_attachments(
                    document, configured_attachments, detail_url=detail_url
                )
                operations.append("MergeConfiguredOfficialAttachments")

            attachment_responses, attachment_errors = _download_attachments(
                document,
                client=client,
                detail_url=detail_url,
                policy=attachment_policy,
                browser_session=browser_session or owned_browser,
            )
            if document.attachment_inventory:
                operations.append("InspectAttachmentInventory")
            if attachment_responses:
                operations.append("DownloadAttachments")
            if attachment_errors:
                LOGGER.warning("Attachment errors: %s", " | ".join(attachment_errors))
            result = write_snapshot(
                output_root=output_root,
                document=document,
                expected_document_number=document_number,
                detail_url=detail_url,
                sitemap_url=sitemap_url,
                captures=captures,
                rendered_html=rendered_html,
                expected_articles=expected_articles,
                attachment_responses=attachment_responses,
                attachment_policy=attachment_policy,
                ingestion_contract=ingestion_contract,
                operations=operations,
                force_snapshot=force_snapshot,
            )
            if owned_browser is not None:
                owned_browser.close()
            return result
        except Exception as exc:
            if owned_browser is not None:
                owned_browser.close()
            diagnostics = write_failure_diagnostics(
                output_root=output_root,
                document_number=document_number,
                detail_url=detail_url,
                rendered_html=rendered_html,
                captures=captures,
                error=exc,
            )
            raise VbplPortalError(f"{exc}. Diagnostics: {diagnostics}") from exc


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VbplPortalError(f"Expected JSON object: {path}")
    return value


def write_run_report(path: Path, payload: Mapping[str, Any]) -> None:
    write_json(path, dict(payload))


def doctor(
    *,
    sitemap_url: str,
    timeout: float,
    portal_url: str | None,
    document_number: str | None,
) -> int:
    client = RetryingHttpClient(timeout=timeout)
    checks: dict[str, Any] = {}
    try:
        kind, urls = parse_sitemap(
            client.fetch(sitemap_url, accept="application/xml,text/xml,*/*").body
        )
        checks["sitemap"] = {"ok": True, "kind": kind, "entries": len(urls)}
    except Exception as exc:
        checks["sitemap"] = {"ok": False, "error": str(exc)}
    if portal_url:
        try:
            capture = fetch_gateway_payload_direct(portal_url, client=client)
            if capture is None:
                raise VbplPortalError("Direct gateway was unavailable")
            if document_number:
                document = extract_document_from_payloads(
                    [capture.payload], document_number, detail_url=portal_url
                )
                checks["gateway"] = {
                    "ok": True,
                    "document_number": document.document_number,
                    "item_id": document.item_id,
                    "response_bytes": len(capture.body),
                }
            else:
                checks["gateway"] = {"ok": True, "response_bytes": len(capture.body)}
        except Exception as exc:
            checks["gateway"] = {"ok": False, "error": str(exc)}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            executable = playwright.chromium.executable_path
            checks["playwright"] = {
                "ok": Path(executable).exists(),
                "chromium": executable,
            }
    except Exception as exc:
        checks["playwright"] = {"ok": False, "error": str(exc)}
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    required = [checks["sitemap"]]
    if portal_url:
        required.append(checks["gateway"])
    return 0 if all(check.get("ok") for check in required) else 2


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 3)


def soak_test(
    *,
    document_number: str,
    portal_url: str,
    expected_articles: int | None,
    iterations: int,
    interval_seconds: float,
    timeout: float,
    retries: int,
    backoff_seconds: float,
    headed: bool,
    settle_seconds: float,
    api_substring: str,
    max_failure_rate: float,
    report_path: Path | None,
) -> int:
    """Repeatedly validate one live document without publishing snapshots.

    This is a pre-release reliability gate: identity, article sequence, response
    parsing, and content hash must remain stable across repeated requests.
    """

    if iterations < 1:
        raise VbplPortalError("--iterations must be at least 1")
    if not 0 <= max_failure_rate <= 1:
        raise VbplPortalError("--max-failure-rate must be between 0 and 1")
    client = RetryingHttpClient(
        timeout=timeout, retries=retries, backoff_seconds=backoff_seconds
    )
    browser = BrowserSession(
        headed=headed,
        timeout=timeout,
        settle_seconds=settle_seconds,
        api_substring=api_substring,
    )
    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    hashes: set[str] = set()
    try:
        for index in range(iterations):
            started = time.monotonic()
            try:
                capture = fetch_gateway_payload_direct(portal_url, client=client)
                acquisition = "direct_gateway"
                captures: list[CaptureRecord]
                if capture is not None:
                    captures = [capture]
                else:
                    _, captures = browser.fetch_detail(portal_url)
                    acquisition = "browser_xhr"
                document = extract_document_from_payloads(
                    [item.payload for item in captures],
                    document_number,
                    detail_url=portal_url,
                )
                detail_id = extract_item_id_from_detail_url(portal_url)
                if detail_id and document.item_id is not None and str(detail_id) != str(document.item_id):
                    raise VbplPortalError(
                        f"Item-id mismatch: detail={detail_id}, payload={document.item_id}"
                    )
                article_report = article_sequence_report(
                    document.full_text_html, expected_articles
                )
                if not article_report["valid"]:
                    raise VbplPortalError(
                        f"Article sequence invalid: {article_report}"
                    )
                content_hash = sha256_bytes(
                    document.full_text_html.encode("utf-8")
                )
                hashes.add(content_hash)
                elapsed = (time.monotonic() - started) * 1000
                latencies.append(elapsed)
                results.append(
                    {
                        "iteration": index + 1,
                        "status": "success",
                        "acquisition": acquisition,
                        "elapsed_ms": round(elapsed, 3),
                        "content_sha256": content_hash,
                        "article_count": article_report["detected_count"],
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "iteration": index + 1,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "elapsed_ms": round(
                            (time.monotonic() - started) * 1000, 3
                        ),
                    }
                )
            if index + 1 < iterations and interval_seconds > 0:
                time.sleep(interval_seconds)
    finally:
        browser.close()

    failures = sum(item["status"] == "failed" for item in results)
    failure_rate = failures / iterations
    hash_stable = len(hashes) <= 1 and bool(hashes)
    passed = failure_rate <= max_failure_rate and hash_stable
    report = {
        "schema_version": "vbpl-live-soak-v1",
        "document_number": document_number,
        "portal_url": portal_url,
        "run_at": utc_now(),
        "iterations": iterations,
        "successes": iterations - failures,
        "failures": failures,
        "failure_rate": round(failure_rate, 6),
        "max_failure_rate": max_failure_rate,
        "unique_content_hashes": sorted(hashes),
        "content_hash_stable": hash_stable,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": round(max(latencies), 3) if latencies else None,
        },
        "passed": passed,
        "results": results,
    }
    if report_path is not None:
        write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--sitemap-url", default=DEFAULT_SITEMAP)
    doctor_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    doctor_parser.add_argument("--portal-url")
    doctor_parser.add_argument("--document-number")

    soak_parser = sub.add_parser("soak")
    soak_parser.add_argument("document_number")
    soak_parser.add_argument("--portal-url", required=True)
    soak_parser.add_argument("--expected-articles", type=int)
    soak_parser.add_argument("--iterations", type=int, default=10)
    soak_parser.add_argument("--interval-seconds", type=float, default=2.0)
    soak_parser.add_argument("--max-failure-rate", type=float, default=0.0)
    soak_parser.add_argument("--report", type=Path)
    soak_parser.add_argument("--api-substring", default=DEFAULT_API_SUBSTRING)
    soak_parser.add_argument("--headed", action="store_true")
    soak_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    soak_parser.add_argument("--settle-seconds", type=float, default=5.0)
    soak_parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    soak_parser.add_argument(
        "--backoff-seconds", type=float, default=DEFAULT_BACKOFF_SECONDS
    )

    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("snapshot", type=Path)
    verify_parser.add_argument("--document-number")

    fetch = sub.add_parser("fetch")
    fetch.add_argument("document_number")
    fetch.add_argument("--output", type=Path, required=True)
    fetch.add_argument("--expected-articles", type=int)
    fetch.add_argument("--sitemap-url", default=DEFAULT_SITEMAP)
    fetch.add_argument("--portal-url")
    fetch.add_argument("--item-id")
    fetch.add_argument("--api-substring", default=DEFAULT_API_SUBSTRING)
    fetch.add_argument("--headed", action="store_true")
    fetch.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    fetch.add_argument("--settle-seconds", type=float, default=5.0)
    fetch.add_argument("--attachment-policy", choices=sorted(ATTACHMENT_POLICIES), default="best_effort")
    fetch.add_argument("--require-attachment-bytes", action="store_true", help="Alias for --attachment-policy required")
    fetch.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    fetch.add_argument("--backoff-seconds", type=float, default=DEFAULT_BACKOFF_SECONDS)
    fetch.add_argument("--resume", action="store_true")
    fetch.add_argument("--force-snapshot", action="store_true")

    fetch_config = sub.add_parser("fetch-config")
    fetch_config.add_argument("config", type=Path)
    fetch_config.add_argument("--output", type=Path, required=True)
    fetch_config.add_argument("--headed", action="store_true")
    fetch_config.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    fetch_config.add_argument("--settle-seconds", type=float, default=5.0)
    fetch_config.add_argument("--attachment-policy", choices=sorted(ATTACHMENT_POLICIES))
    fetch_config.add_argument("--require-attachment-bytes", action="store_true")
    fetch_config.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    fetch_config.add_argument("--backoff-seconds", type=float, default=DEFAULT_BACKOFF_SECONDS)
    fetch_config.add_argument("--delay-seconds", type=float, default=2.0)
    fetch_config.add_argument("--resume", action="store_true")
    fetch_config.add_argument("--force-snapshot", action="store_true")
    fetch_config.add_argument("--continue-on-error", action="store_true")
    fetch_config.add_argument("--run-report", type=Path)
    fetch_config.add_argument("--sitemap-cache-ttl-hours", type=float, default=DEFAULT_SITEMAP_CACHE_TTL_HOURS)
    fetch_config.add_argument("--refresh-sitemap", action="store_true")
    return parser


def _resolve_policy(args: argparse.Namespace, item: Mapping[str, Any] | None = None) -> str:
    if getattr(args, "require_attachment_bytes", False):
        return "required"
    cli_policy = getattr(args, "attachment_policy", None)
    if cli_policy:
        return str(cli_policy)
    if item and item.get("attachment_policy"):
        return str(item["attachment_policy"])
    return "best_effort"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "doctor":
        return doctor(
            sitemap_url=args.sitemap_url,
            timeout=args.timeout,
            portal_url=args.portal_url,
            document_number=args.document_number,
        )
    if args.command == "soak":
        return soak_test(
            document_number=args.document_number,
            portal_url=args.portal_url,
            expected_articles=args.expected_articles,
            iterations=args.iterations,
            interval_seconds=args.interval_seconds,
            timeout=args.timeout,
            retries=args.retries,
            backoff_seconds=args.backoff_seconds,
            headed=args.headed,
            settle_seconds=args.settle_seconds,
            api_substring=args.api_substring,
            max_failure_rate=args.max_failure_rate,
            report_path=args.report,
        )
    if args.command == "verify":
        result = verify_snapshot(
            args.snapshot, expected_document_number=args.document_number
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "fetch":
        result = fetch_one(
            document_number=args.document_number,
            output_root=args.output,
            expected_articles=args.expected_articles,
            sitemap_url=args.sitemap_url,
            portal_url=args.portal_url,
            item_id=args.item_id,
            api_substring=args.api_substring,
            headed=args.headed,
            timeout=args.timeout,
            settle_seconds=args.settle_seconds,
            attachment_policy=_resolve_policy(args),
            configured_attachments=(),
            retries=args.retries,
            backoff_seconds=args.backoff_seconds,
            resume=args.resume,
            force_snapshot=args.force_snapshot,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    config = load_json(args.config)
    source = config.get("source") or {}
    portal = source.get("portal") or {}
    sitemap_url = str(portal.get("sitemap_url") or DEFAULT_SITEMAP)
    api_substring = str(portal.get("api_url_substring") or DEFAULT_API_SUBSTRING)
    all_documents = list(config.get("documents") or [])
    external_documents = [
        item for item in all_documents
        if str(item.get("source_adapter") or "vbpl") != "vbpl"
    ]
    documents = [
        item for item in all_documents
        if str(item.get("source_adapter") or "vbpl") == "vbpl"
    ]
    client = RetryingHttpClient(
        timeout=args.timeout,
        retries=args.retries,
        backoff_seconds=args.backoff_seconds,
    )
    missing_locators = [
        str(item.get("document_number"))
        for item in documents
        if not item.get("portal_url") and item.get("item_id") in (None, "")
    ]
    if missing_locators:
        raise VbplPortalError(
            "VBPL registry is incomplete; missing item_id/portal_url for: "
            + ", ".join(missing_locators)
        )
    needs_sitemap = any(
        not item.get("portal_url") and item.get("item_id") in (None, "")
        for item in documents
    )
    sitemap_urls = (
        load_or_collect_sitemap_urls(
            output_root=args.output,
            sitemap_url=sitemap_url,
            client=client,
            ttl_hours=args.sitemap_cache_ttl_hours,
            force_refresh=args.refresh_sitemap,
        )
        if needs_sitemap
        else []
    )
    run_id = uuid.uuid4().hex
    started_at = utc_now()
    report_path = args.run_report or (args.output / "run_manifest.json")
    report: dict[str, Any] = {
        "schema_version": RUN_SCHEMA,
        "run_id": run_id,
        "config": str(args.config),
        "started_at": started_at,
        "finished_at": None,
        "status": "running",
        "total": len(documents),
        "external_source_documents": [
            {
                "document_number": str(item.get("document_number") or ""),
                "source_adapter": str(item.get("source_adapter") or ""),
                "official_page_url": item.get("official_page_url"),
            }
            for item in external_documents
        ],
        "success": 0,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
        "results": [],
    }
    write_run_report(report_path, report)
    browser = BrowserSession(
        headed=args.headed,
        timeout=args.timeout,
        settle_seconds=args.settle_seconds,
        api_substring=api_substring,
    )
    try:
        # BrowserSession starts lazily only if a direct-gateway fetch needs fallback,
        # then reuses one Chromium context for the remaining batch.
        for index, item in enumerate(documents):
            number = str(item["document_number"])
            item_started = time.monotonic()
            try:
                # fetch_one will create a browser itself if no reusable session exists.
                result = fetch_one(
                    document_number=number,
                    output_root=args.output,
                    expected_articles=(int(item["expected_articles"]) if item.get("expected_articles") is not None else None),
                    sitemap_url=sitemap_url,
                    portal_url=(str(item["portal_url"]) if item.get("portal_url") else None),
                    item_id=(str(item["item_id"]) if item.get("item_id") not in (None, "") else None),
                    api_substring=api_substring,
                    headed=args.headed,
                    timeout=args.timeout,
                    settle_seconds=args.settle_seconds,
                    attachment_policy=_resolve_policy(args, item),
                    configured_attachments=tuple(item.get("official_attachments") or ()),
                    sitemap_urls=sitemap_urls,
                    retries=args.retries,
                    backoff_seconds=args.backoff_seconds,
                    browser_session=browser,
                    resume=args.resume,
                    force_snapshot=args.force_snapshot,
                )
                status = str(result.get("status") or "created")
                if status == "created":
                    report["success"] += 1
                elif status == "unchanged":
                    report["unchanged"] += 1
                elif status == "skipped_valid":
                    report["skipped"] += 1
                report["results"].append(
                    {
                        "document_number": number,
                        "status": status,
                        "snapshot_dir": result.get("snapshot_dir"),
                        "elapsed_ms": round((time.monotonic() - item_started) * 1000, 3),
                    }
                )
            except Exception as exc:
                report["failed"] += 1
                report["results"].append(
                    {
                        "document_number": number,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "elapsed_ms": round((time.monotonic() - item_started) * 1000, 3),
                    }
                )
                if not args.continue_on_error:
                    raise
            finally:
                write_run_report(report_path, report)
            if index + 1 < len(documents) and args.delay_seconds > 0:
                time.sleep(args.delay_seconds)
    finally:
        browser.close()
        report["finished_at"] = utc_now()
        report["status"] = "failed" if report["failed"] else "completed"
        write_run_report(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["failed"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VbplPortalError as exc:
        print(f"VBPL portal error: {exc}", file=sys.stderr)
        raise SystemExit(2)
