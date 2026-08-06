"""Read complete legal articles from the immutable JSONL retrieval corpus."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from ..core.paths import resolve_project_path
from ..schemas.document import (
    ArticleListResponse,
    ArticleSummary,
    DocumentDetail,
    DocumentListResponse,
    DocumentSummary,
    PaginationMeta,
)
from ..schemas.source import LegalArticleResponse, LegalArticleUnit
from .legal_citation import build_citation_metadata
from .official_sources import (
    OfficialSourceRegistry,
    get_official_source_registry,
    original_source_url,
)


_ARTICLE_CODE_RE = re.compile(r"^[\w.-]{1,100}$", re.UNICODE)


class SourceCatalogError(RuntimeError):
    """Raised when the local legal corpus cannot be read safely."""


class ArticleNotFoundError(LookupError):
    """Raised when an article code is valid but absent from the corpus."""


class DocumentNotFoundError(LookupError):
    """Raised when a document_id does not exist in the corpus."""


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _optional_text(value)
    return [text] if text else []


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _body_text(chunk: Mapping[str, Any]) -> str | None:
    body = _optional_text(chunk.get("body_text"))
    if body:
        return body
    content = _optional_text(chunk.get("content"))
    if not content:
        return None
    # Retrieval chunks prefix metadata before a blank line. Remove only that
    # generated display header; the legal text following it is left untouched.
    _, separator, legal_text = content.partition("\n\n")
    return legal_text.strip() if separator and legal_text.strip() else content


def _unit_label(chunk: Mapping[str, Any]) -> str:
    table_index = _optional_int(chunk.get("table_index"))
    clause = _optional_text(chunk.get("clause_number"))
    points = _string_list(chunk.get("point_labels") or chunk.get("point"))
    segment = _optional_int(chunk.get("segment_index"))
    unit_type = _optional_text(
        chunk.get("unit_type") or chunk.get("chunk_type")
    ) or "legal_unit"

    if table_index is not None:
        label = f"Bảng {table_index}"
    elif clause and points:
        label = f"Khoản {clause} · Điểm {', '.join(points)}"
    elif clause:
        label = f"Khoản {clause}"
    elif points:
        label = f"Điểm {', '.join(points)}"
    elif unit_type == "preamble":
        label = "Phần mở đầu"
    else:
        label = "Nội dung điều"
    if segment is not None and segment > 1:
        label += f" · Phần {segment}"
    return label


def _normalize_for_search(text: str) -> str:
    """Casefold and strip diacritics for accent-insensitive Vietnamese search.

    Handles đ/Đ (U+0111/U+0110) explicitly because NFD decomposition does not
    strip the stroke — these require direct transliteration to d.
    """
    # Replace stroke-d variants which have no NFD combining sequence.
    text = text.replace("đ", "d").replace("Đ", "D")
    nfd = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def _safe_http_url(url: str | None) -> str | None:
    """Return url only if it is a valid http/https URL with a non-empty host."""
    if not url:
        return None
    try:
        parsed = urlsplit(url)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None
    return url


def _resolve_official_url(
    official: Any,
    doc: dict[str, Any],
) -> str | None:
    """Return the best available public URL, validating scheme before returning."""
    if official is not None:
        candidate = _safe_http_url(getattr(official, "canonical_url", None))
        if candidate:
            return candidate
    # Fallback: first valid http/https URL from the chunk's source_urls list.
    for raw in doc.get("source_urls", []):
        candidate = _safe_http_url(raw)
        if candidate:
            return candidate
    return None


def _paginate(total: int, page: int, page_size: int) -> PaginationMeta:
    """Compute pagination metadata, returning total_pages=0 when total is 0."""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    return PaginationMeta(
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


class LegalSourceCatalog:
    def __init__(
        self,
        *,
        chunks_path: str | Path,
        official_sources: OfficialSourceRegistry | None = None,
        canonical_articles_path: str | Path | None = None,
    ) -> None:
        self.chunks_path = resolve_project_path(chunks_path)
        self.official_sources = (
            official_sources
            if official_sources is not None
            else get_official_source_registry()
        )
        self._by_article_code = self._load_articles()

        # These are populated only when canonical_articles_path is provided.
        self._canonical_meta: dict[str, dict[str, Any]] = {}
        self._corpus_law_as_of: str | None = None
        self._canonical_release_id: str | None = None
        self._article_number_map: dict[str, int] = {}
        self._all_canonical_codes: set[str] = set()
        self._canonical_article_doc_map: dict[str, str] = {}

        if canonical_articles_path is not None:
            (
                self._canonical_meta,
                self._corpus_law_as_of,
                self._canonical_release_id,
                self._article_number_map,
                self._all_canonical_codes,
                self._canonical_article_doc_map,
            ) = self._load_canonical_articles_meta(canonical_articles_path)
            self._check_data_consistency()

        self._document_index = self._build_document_index()

    # ------------------------------------------------------------------
    # Private loaders
    # ------------------------------------------------------------------

    def _load_articles(self) -> dict[str, list[dict[str, Any]]]:
        if not self.chunks_path.is_file():
            raise SourceCatalogError(
                "Không tìm thấy dữ liệu điều luật trong corpus."
            )
        articles: dict[str, list[dict[str, Any]]] = {}
        try:
            with self.chunks_path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise SourceCatalogError(
                            f"Dữ liệu điều luật bị lỗi JSON tại dòng {line_number}."
                        ) from exc
                    if not isinstance(chunk, dict):
                        continue
                    article_code = _optional_text(
                        chunk.get("article_code")
                        or chunk.get("codification_code")
                    )
                    if article_code:
                        articles.setdefault(article_code.casefold(), []).append(chunk)
        except OSError as exc:
            raise SourceCatalogError(
                "Không thể đọc dữ liệu điều luật từ corpus."
            ) from exc
        return articles

    @staticmethod
    def _load_canonical_articles_meta(
        path: str | Path,
    ) -> tuple[dict[str, dict[str, Any]], str | None, str | None, dict[str, int], set[str], dict[str, str]]:
        """Read canonical_articles.json for document-level fields absent from chunks.

        When path is provided this method is *fail-closed*: any structural
        problem raises SourceCatalogError so the caller cannot proceed with
        stale or mismatched data.
        """
        resolved = resolve_project_path(path)
        if not resolved.is_file():
            raise SourceCatalogError(
                "Không tìm thấy canonical_articles.json trong thư mục release."
            )
        try:
            raw = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise SourceCatalogError(
                "Không thể đọc canonical_articles.json."
            ) from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SourceCatalogError(
                "canonical_articles.json không phải JSON hợp lệ."
            ) from exc
        if not isinstance(payload, dict):
            raise SourceCatalogError(
                "canonical_articles.json: root phải là một JSON object."
            )
        meta_block = payload.get("metadata")
        if meta_block is not None and not isinstance(meta_block, dict):
            raise SourceCatalogError(
                "canonical_articles.json: trường 'metadata' phải là object."
            )
        corpus_law_as_of: str | None = None
        canonical_release_id: str | None = None
        if isinstance(meta_block, dict):
            corpus_law_as_of = _optional_text(meta_block.get("law_as_of"))
            canonical_release_id = _optional_text(
                meta_block.get("release_id") or meta_block.get("corpus_release_id")
            )

        articles = payload.get("articles")
        if not isinstance(articles, list) or len(articles) == 0:
            raise SourceCatalogError(
                "canonical_articles.json: trường 'articles' phải là list không rỗng."
            )

        meta: dict[str, dict[str, Any]] = {}
        article_number_map: dict[str, int] = {}
        all_codes: set[str] = set()
        article_doc_map: dict[str, str] = {}
        for idx, art in enumerate(articles):
            if not isinstance(art, dict):
                raise SourceCatalogError(
                    f"canonical_articles.json: phần tử #{idx} không phải object."
                )
            doc_id = _optional_text(art.get("document_id"))
            code = _optional_text(art.get("article_code"))
            if not doc_id or not code:
                raise SourceCatalogError(
                    f"canonical_articles.json: phần tử #{idx} thiếu "
                    "'document_id' hoặc 'article_code'."
                )
            code_key = code.casefold()
            if code_key in article_doc_map:
                raise SourceCatalogError(
                    f"Lỗi dữ liệu: article_code '{code}' bị trùng lặp trong "
                    "canonical_articles.json."
                )
            article_doc_map[code_key] = doc_id
            all_codes.add(code_key)
            if doc_id not in meta:
                meta[doc_id] = {
                    "document_title": _optional_text(art.get("document_title")),
                    "issuing_authority": _optional_text(art.get("issuing_authority")),
                    "issued_at": _optional_text(art.get("issued_at")),
                    "effective_from": _optional_text(art.get("effective_from")),
                    "legal_status": _optional_text(art.get("legal_status")),
                    "law_as_of": corpus_law_as_of,
                }
            num = _optional_int(art.get("article_number"))
            if num is not None:
                article_number_map[code_key] = num

        return (
            meta,
            corpus_law_as_of,
            canonical_release_id,
            article_number_map,
            all_codes,
            article_doc_map,
        )

    def _check_data_consistency(self) -> None:
        """Raise SourceCatalogError if article codes or document bindings diverge."""
        codes_in_chunks: set[str] = set(self._by_article_code.keys())
        codes_in_articles: set[str] = self._all_canonical_codes

        only_in_chunks = codes_in_chunks - codes_in_articles
        only_in_articles = codes_in_articles - codes_in_chunks
        if only_in_chunks or only_in_articles:
            raise SourceCatalogError(
                f"Corpus không nhất quán: {len(only_in_chunks)} article_code "
                f"chỉ có trong JSONL, {len(only_in_articles)} chỉ có trong "
                "canonical_articles.json. Hai file phải cùng một release."
            )

        for code_key, chunks in self._by_article_code.items():
            first_doc_id = _optional_text(chunks[0].get("document_id"))
            for chunk in chunks:
                c_doc_id = _optional_text(chunk.get("document_id"))
                if not c_doc_id:
                    raise SourceCatalogError(
                        f"Chunk thuộc article_code '{code_key}' thiếu document_id."
                    )
                if c_doc_id != first_doc_id:
                    raise SourceCatalogError(
                        f"Cùng article_code '{code_key}' nhưng các chunk có document_id "
                        f"không nhất quán: '{first_doc_id}' vs '{c_doc_id}'."
                    )
            canonical_doc_id = self._canonical_article_doc_map.get(code_key)
            if first_doc_id != canonical_doc_id:
                raise SourceCatalogError(
                    f"document_id không đồng nhất cho article_code '{code_key}': "
                    f"JSONL có '{first_doc_id}', canonical_articles.json có '{canonical_doc_id}'."
                )

    def _build_document_index(self) -> dict[str, dict[str, Any]]:
        """Build a per-document index from the already-loaded article chunks.

        Returns a dict keyed by document_id.  Each value holds the
        representative first-chunk metadata plus a sorted list of unique
        article codes belonging to that document.
        """
        docs: dict[str, dict[str, Any]] = {}
        seen_codes: dict[str, set[str]] = {}

        for code_key, chunks in self._by_article_code.items():
            first = chunks[0]
            doc_id = _optional_text(first.get("document_id"))
            if not doc_id:
                continue
            if doc_id not in docs:
                docs[doc_id] = {
                    "document_id": doc_id,
                    "document_number": _optional_text(first.get("document_number")) or "",
                    "source_type": _optional_text(first.get("source_type")),
                    "source_adapter": _optional_text(first.get("source_adapter")),
                    "source_document_id": _optional_text(first.get("source_document_id")),
                    "source_urls": _string_list(first.get("source_urls")),
                    "article_codes": [],
                }
                seen_codes[doc_id] = set()
            # Recover canonical casing from the chunk itself.
            canonical_code = _optional_text(
                first.get("article_code") or first.get("codification_code")
            )
            if canonical_code and code_key not in seen_codes[doc_id]:
                seen_codes[doc_id].add(code_key)
                docs[doc_id]["article_codes"].append(
                    {
                        "article_code": canonical_code,
                        "article_number": self._article_number_map.get(code_key),
                        "article_title": _optional_text(first.get("article_title")),
                        "heading": _optional_text(first.get("heading")),
                        "chapter_number": _optional_text(first.get("chapter_number")),
                        "chapter_title": _optional_text(first.get("chapter_title")),
                        "section_number": _optional_text(first.get("section_number")),
                        "section_title": _optional_text(first.get("section_title")),
                    }
                )

        # Sort articles within each document by article_number (int) then code.
        for doc in docs.values():
            doc["article_codes"].sort(
                key=lambda a: (
                    a["article_number"] if a["article_number"] is not None else 99999,
                    a["article_code"],
                )
            )

        return docs

    # ------------------------------------------------------------------
    # Public: existing article lookup (unchanged semantics)
    # ------------------------------------------------------------------

    def get_article(self, article_code: str) -> LegalArticleResponse:
        normalized = str(article_code).strip()
        if not _ARTICLE_CODE_RE.fullmatch(normalized):
            raise ValueError("Mã pháp điển không hợp lệ")
        chunks = self._by_article_code.get(normalized.casefold())
        if not chunks:
            raise ArticleNotFoundError(normalized)

        first = chunks[0]
        citation = build_citation_metadata(first)
        official_record = self.official_sources.resolve(first)
        original_urls: list[str] = []
        units: list[LegalArticleUnit] = []
        for chunk in chunks:
            for url in _string_list(chunk.get("source_urls")):
                if url not in original_urls:
                    original_urls.append(url)
            fallback_url = original_source_url(chunk)
            if fallback_url and fallback_url not in original_urls:
                original_urls.append(fallback_url)

            content = _body_text(chunk)
            if not content:
                continue
            units.append(LegalArticleUnit(
                chunk_id=_optional_text(chunk.get("chunk_id"))
                or f"{normalized}-{len(units) + 1}",
                label=_unit_label(chunk),
                unit_type=_optional_text(
                    chunk.get("unit_type") or chunk.get("chunk_type")
                ) or "legal_unit",
                clause_number=_optional_text(chunk.get("clause_number")),
                point_labels=_string_list(
                    chunk.get("point_labels") or chunk.get("point")
                ),
                table_index=_optional_int(chunk.get("table_index")),
                segment_index=_optional_int(chunk.get("segment_index")),
                content=content,
            ))

        official_url = (
            official_record.canonical_url
            if official_record
            else (original_urls[0] if original_urls else None)
        )
        return LegalArticleResponse(
            article_code=_optional_text(
                first.get("article_code") or first.get("codification_code")
            ) or normalized,
            article_number=citation.article_number,
            article_title=_optional_text(first.get("article_title")),
            citation_label=citation.label,
            document_title=citation.document_title,
            document_number=citation.document_number,
            source_type=_optional_text(first.get("source_type")),
            source_document_id=_optional_text(first.get("source_document_id")),
            source_note_text=_optional_text(first.get("source_note_text")),
            topic_code=_optional_text(first.get("topic_code")),
            topic_name=_optional_text(first.get("topic_name")),
            chapter_number=_optional_text(first.get("chapter_number")),
            chapter_title=_optional_text(first.get("chapter_title")),
            section_number=_optional_text(first.get("section_number")),
            section_title=_optional_text(first.get("section_title")),
            official_url=official_url,
            original_source_urls=original_urls,
            url_status=official_record.url_status if official_record else None,
            url_last_checked_at=(
                official_record.last_checked_at if official_record else None
            ),
            chunk_count=len(units),
            units=units,
        )

    # ------------------------------------------------------------------
    # Public: document library
    # ------------------------------------------------------------------

    def _build_document_summary(
        self,
        doc: dict[str, Any],
    ) -> DocumentSummary:
        """Assemble a DocumentSummary from index + registry + canonical meta."""
        doc_id = doc["document_id"]
        official = self.official_sources.resolve(doc)
        canon = self._canonical_meta.get(doc_id, {})

        title = (
            canon.get("document_title")
            or (official.title if official else None)
        )
        return DocumentSummary(
            document_id=doc_id,
            document_number=doc["document_number"],
            title=title,
            source_type=doc["source_type"],
            issuing_authority=canon.get("issuing_authority"),
            issued_date=canon.get("issued_at"),
            effective_date=canon.get("effective_from"),
            legal_status_code=canon.get("legal_status"),
            article_count=len(doc["article_codes"]),
            official_url=_resolve_official_url(official, doc),
            source_adapter=doc["source_adapter"],
        )

    def _build_document_detail(
        self,
        doc: dict[str, Any],
    ) -> DocumentDetail:
        summary = self._build_document_summary(doc)
        doc_id = doc["document_id"]
        official = self.official_sources.resolve(doc)
        canon = self._canonical_meta.get(doc_id, {})
        return DocumentDetail(
            **summary.model_dump(),
            law_as_of=canon.get("law_as_of") or self._corpus_law_as_of,
            url_status=official.url_status if official else None,
            url_last_checked_at=official.last_checked_at if official else None,
        )

    def list_documents(
        self,
        *,
        q: str | None = None,
        document_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> DocumentListResponse:
        from ..core.config import settings

        # Normalize inputs.
        q_stripped = (q or "").strip() or None
        dt_stripped = (document_type or "").strip() or None

        docs = list(self._document_index.values())

        # Filter by document_type (source_type).
        if dt_stripped:
            dt_lower = dt_stripped.casefold()
            docs = [
                d for d in docs
                if d["source_type"] and d["source_type"].casefold() == dt_lower
            ]

        # Filter by query string (number, title, accent-insensitive).
        if q_stripped:
            q_norm = _normalize_for_search(q_stripped)
            filtered = []
            for d in docs:
                doc_id = d["document_id"]
                canon = self._canonical_meta.get(doc_id, {})
                title = canon.get("document_title") or ""
                haystack = _normalize_for_search(
                    d["document_number"] + " " + title
                )
                if q_norm in haystack:
                    filtered.append(d)
            docs = filtered

        # Stable sort: source_type then document_id.
        docs.sort(key=lambda d: (d["source_type"] or "", d["document_id"]))

        total = len(docs)
        pagination = _paginate(total, page, page_size)
        start = (pagination.page - 1) * pagination.page_size
        page_docs = docs[start : start + pagination.page_size]

        return DocumentListResponse(
            release_id=self._canonical_release_id or settings.corpus_release_id,
            law_as_of=self._corpus_law_as_of,
            pagination=pagination,
            documents=[self._build_document_summary(d) for d in page_docs],
        )

    def get_document(self, document_id: str) -> DocumentDetail:
        doc = self._document_index.get(document_id)
        if doc is None:
            raise DocumentNotFoundError(document_id)
        return self._build_document_detail(doc)

    def list_document_articles(
        self,
        document_id: str,
        *,
        q: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> ArticleListResponse:
        doc = self._document_index.get(document_id)
        if doc is None:
            raise DocumentNotFoundError(document_id)

        # Normalize query.
        q_stripped = (q or "").strip() or None

        articles: list[dict[str, Any]] = list(doc["article_codes"])

        if q_stripped:
            q_norm = _normalize_for_search(q_stripped)
            articles = [
                a for a in articles
                if q_norm in _normalize_for_search(
                    str(a["article_number"] or "")
                    + " "
                    + (a["heading"] or "")
                    + " "
                    + (a["article_title"] or "")
                )
            ]

        total = len(articles)
        pagination = _paginate(total, page, page_size)
        start = (pagination.page - 1) * pagination.page_size
        page_articles = articles[start : start + pagination.page_size]

        return ArticleListResponse(
            document_id=document_id,
            document_number=doc["document_number"],
            pagination=pagination,
            articles=[
                ArticleSummary(
                    article_code=a["article_code"],
                    article_number=a["article_number"],
                    title=a["article_title"],
                    heading=a["heading"],
                    chapter_number=a["chapter_number"],
                    chapter_title=a["chapter_title"],
                    section_number=a["section_number"],
                    section_title=a["section_title"],
                )
                for a in page_articles
            ],
        )


@lru_cache(maxsize=1)
def get_source_catalog() -> LegalSourceCatalog:
    from ..core.config import settings

    chunks_path = resolve_project_path(settings.legal_chunks_path)
    canonical_path = resolve_project_path(settings.canonical_articles_path)

    # Both files must live in the same release directory.
    if chunks_path.parent != canonical_path.parent:
        raise SourceCatalogError(
            "canonical_chunks.jsonl và canonical_articles.json phải cùng "
            "thư mục release. Kiểm tra cấu hình legal_chunks_path và "
            "canonical_articles_path."
        )

    catalog = LegalSourceCatalog(
        chunks_path=chunks_path,
        official_sources=get_official_source_registry(
            settings.official_sources_path
        ),
        canonical_articles_path=canonical_path,
    )

    if (
        catalog._canonical_release_id is not None
        and catalog._canonical_release_id != settings.corpus_release_id
    ):
        raise SourceCatalogError(
            "Release ID trong canonical_articles.json không khớp với "
            "cấu hình corpus_release_id."
        )

    return catalog
