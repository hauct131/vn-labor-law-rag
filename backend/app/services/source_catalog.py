"""Read complete legal articles from the immutable JSONL retrieval corpus."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from ..core.paths import resolve_project_path
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


class LegalSourceCatalog:
    def __init__(
        self,
        *,
        chunks_path: str | Path,
        official_sources: OfficialSourceRegistry | None = None,
    ) -> None:
        self.chunks_path = resolve_project_path(chunks_path)
        self.official_sources = (
            official_sources
            if official_sources is not None
            else get_official_source_registry()
        )
        self._by_article_code = self._load_articles()

    def _load_articles(self) -> dict[str, list[dict[str, Any]]]:
        if not self.chunks_path.is_file():
            raise SourceCatalogError(
                f"Không tìm thấy dữ liệu điều luật: {self.chunks_path}"
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
                            "Dữ liệu điều luật bị lỗi JSON tại dòng "
                            f"{line_number}: {self.chunks_path}"
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
                f"Không thể đọc dữ liệu điều luật: {self.chunks_path}"
            ) from exc
        return articles

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


@lru_cache(maxsize=1)
def get_source_catalog() -> LegalSourceCatalog:
    from ..core.config import settings

    return LegalSourceCatalog(
        chunks_path=settings.legal_chunks_path,
        official_sources=get_official_source_registry(
            settings.official_sources_path
        ),
    )
