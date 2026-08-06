"""Build human-readable citations from Pháp điển chunk metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


_ARTICLE_RE = re.compile(
    r"\bĐiều\s+(?P<number>\d+(?:[a-zA-ZđĐ])?)\b",
    re.IGNORECASE,
)
_DOCUMENT_RE = re.compile(
    r"\b(?P<kind>Bộ\s+luật|Luật|Nghị\s+định|Thông\s+tư)"
    r"\s+số\s+"
    r"(?P<number>\d+/\d{4}/[0-9A-ZÀ-ỸĐ-]+)",
    re.IGNORECASE,
)

# The current corpus contains one primary code-level document. Its notes use
# the generic phrase "Bộ luật số ...", so the canonical short title must be
# restored explicitly for a useful legal citation.
_CANONICAL_DOCUMENT_TITLES = {
    "45/2019/QH14": "Bộ luật Lao động",
}

_SOURCE_TYPE_TITLES = {
    "LQ": "Văn bản luật",
    "NĐ": "Nghị định",
    "TT": "Thông tư",
}

_DOCUMENT_KIND_TITLES = {
    "bộ luật": "Bộ luật",
    "luật": "Luật",
    "nghị định": "Nghị định",
    "thông tư": "Thông tư",
}


@dataclass(frozen=True, slots=True)
class CitationMetadata:
    article_number: str | None
    document_title: str | None
    document_number: str | None
    label: str


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _article_number_from_code(article_code: str | None) -> str | None:
    if not article_code:
        return None
    final_part = article_code.rsplit(".", 1)[-1].strip()
    if re.fullmatch(r"\d+(?:[a-zA-ZđĐ])?", final_part):
        return final_part
    return None


def build_citation_metadata(payload: Mapping[str, Any]) -> CitationMetadata:
    """Return a stable display citation without exposing machine IDs as titles."""
    source_note = _optional_text(payload.get("source_note_text")) or ""
    article_code = _optional_text(
        payload.get("article_code") or payload.get("codification_code")
    )

    article_match = _ARTICLE_RE.search(source_note)
    article_number = (
        _optional_text(payload.get("article_number"))
        or (article_match.group("number") if article_match else None)
        or _article_number_from_code(article_code)
    )

    document_match = _DOCUMENT_RE.search(source_note)
    document_number = _optional_text(payload.get("document_number"))
    if document_number is None and document_match:
        document_number = document_match.group("number").upper()

    document_title = _optional_text(payload.get("document_title"))
    if document_title is None and document_number:
        document_title = _CANONICAL_DOCUMENT_TITLES.get(document_number)
    if document_title is None and document_match:
        normalized_kind = " ".join(
            document_match.group("kind").casefold().split()
        )
        document_title = _DOCUMENT_KIND_TITLES.get(normalized_kind)
    if document_title is None:
        source_type = (_optional_text(payload.get("source_type")) or "").upper()
        document_title = _SOURCE_TYPE_TITLES.get(source_type)

    if article_number and document_title and document_number:
        label = (
            f"Điều {article_number} {document_title} "
            f"số {document_number}"
        )
    elif article_number and document_title:
        label = f"Điều {article_number} {document_title}"
    elif article_number and document_number:
        label = f"Điều {article_number} văn bản số {document_number}"
    elif article_number:
        label = f"Điều {article_number}"
    elif article_code:
        label = f"Mã pháp điển {article_code}"
    else:
        label = "Nguồn pháp luật"

    return CitationMetadata(
        article_number=article_number,
        document_title=document_title,
        document_number=document_number,
        label=label,
    )
