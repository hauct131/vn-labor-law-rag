"""Resolve stale corpus URLs to a small, centrally maintained registry."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


logger = logging.getLogger(__name__)

_ITEM_ID_RE = re.compile(r"(?:[?&]ItemID=)(\d+)", re.IGNORECASE)


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


def _validate_url(value: Any, *, field_name: str) -> str:
    url = _optional_text(value)
    if url is None:
        raise ValueError(f"{field_name} không được để trống")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} không phải URL HTTP(S) hợp lệ")
    return url


@dataclass(frozen=True, slots=True)
class OfficialSourceRecord:
    source_document_id: str
    item_id: str
    document_number: str
    title: str
    canonical_url: str
    original_url: str
    last_checked_at: str | None = None
    url_status: str | None = None


class OfficialSourceRegistry:
    def __init__(self, records: list[OfficialSourceRecord]) -> None:
        self.records = tuple(records)
        self._by_source_id = {
            record.source_document_id.casefold(): record
            for record in records
        }
        self._by_item_id = {record.item_id: record for record in records}
        self._by_document_number = {
            record.document_number.casefold(): record
            for record in records
        }

    @classmethod
    def empty(cls) -> "OfficialSourceRegistry":
        return cls([])

    @classmethod
    def from_path(cls, path: str | Path) -> "OfficialSourceRegistry":
        registry_path = Path(path)
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Bảng nguồn chính thức không phải JSON hợp lệ: {registry_path}"
            ) from exc

        documents = payload.get("documents") if isinstance(payload, dict) else None
        if not isinstance(documents, dict):
            raise ValueError("Bảng nguồn chính thức phải có object 'documents'")

        records: list[OfficialSourceRecord] = []
        for source_document_id, raw in documents.items():
            if not isinstance(raw, dict):
                raise ValueError(
                    f"Bản ghi {source_document_id!r} phải là một object"
                )
            source_id = _optional_text(source_document_id)
            item_id = _optional_text(raw.get("item_id"))
            document_number = _optional_text(raw.get("document_number"))
            title = _optional_text(raw.get("title"))
            if not all((source_id, item_id, document_number, title)):
                raise ValueError(
                    f"Bản ghi {source_document_id!r} thiếu trường bắt buộc"
                )
            records.append(OfficialSourceRecord(
                source_document_id=source_id,
                item_id=item_id,
                document_number=document_number,
                title=title,
                canonical_url=_validate_url(
                    raw.get("canonical_url"), field_name="canonical_url"
                ),
                original_url=_validate_url(
                    raw.get("original_url"), field_name="original_url"
                ),
                last_checked_at=_optional_text(raw.get("last_checked_at")),
                url_status=_optional_text(raw.get("url_status")),
            ))
        return cls(records)

    def resolve(
        self,
        payload: Mapping[str, Any],
    ) -> OfficialSourceRecord | None:
        source_document_id = _optional_text(payload.get("source_document_id"))
        if source_document_id:
            matched = self._by_source_id.get(source_document_id.casefold())
            if matched is not None:
                return matched

        for url in _string_list(
            payload.get("source_urls") or payload.get("source_url")
        ):
            match = _ITEM_ID_RE.search(url)
            if match:
                matched = self._by_item_id.get(match.group(1))
                if matched is not None:
                    return matched

        document_number = _optional_text(payload.get("document_number"))
        if document_number:
            return self._by_document_number.get(document_number.casefold())
        return None

    def canonical_url_for(
        self,
        payload: Mapping[str, Any],
    ) -> str | None:
        record = self.resolve(payload)
        return record.canonical_url if record else None


@lru_cache(maxsize=8)
def load_official_source_registry(path: str) -> OfficialSourceRegistry:
    return OfficialSourceRegistry.from_path(path)


def get_official_source_registry(
    path: str | None = None,
) -> OfficialSourceRegistry:
    if path is None:
        from ..core.config import settings

        path = settings.official_sources_path
    try:
        return load_official_source_registry(str(path))
    except (OSError, ValueError) as exc:
        # Asking a legal question should still work if this optional registry is
        # temporarily unavailable. The caller can fall back to the corpus URL.
        logger.warning("official_source_registry_unavailable path=%s error=%s", path, exc)
        return OfficialSourceRegistry.empty()


def original_source_url(payload: Mapping[str, Any]) -> str | None:
    urls = _string_list(payload.get("source_urls"))
    if urls:
        return urls[0]
    return _optional_text(payload.get("source_url"))


def resolved_source_url(payload: Mapping[str, Any]) -> str | None:
    registry = get_official_source_registry()
    return registry.canonical_url_for(payload) or original_source_url(payload)
