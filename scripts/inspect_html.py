from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

PARSER_VERSION = "1.2.0"

TOPIC_HEADING_RE = re.compile(
    r"Đề\s*mục\s+(?P<topic_code>\d+\.\d+)"
    r"(?:\s*[-–—:]\s*|\s+)"
    r"(?P<topic_name>.+?)\s*$",
    re.IGNORECASE,
)

ARTICLE_HEADING_RE = re.compile(
    r"^Điều\s+"
    r"(?P<article_code>\d+\.\d+\.(?P<source_type>LQ|NĐ|TT)(?:\.\d+)+)"
    r"\.\s*(?P<title>.+)$",
    re.IGNORECASE,
)
STRUCTURE_MARKER_RE = re.compile(
    r"^(?P<kind>Chương|Mục)\s+(?P<number>.+)$",
    re.IGNORECASE,
)
INTERNAL_TARGET_RE = re.compile(
    r"ViewNoiDungPhapDien\(\s*['\"](?P<target_id>\d+)['\"]\s*\)",
    re.IGNORECASE,
)
TARGET_CODE_RE = re.compile(
    r"\bĐiều\s+"
    r"(?P<target_code>\d+\.\d+\.(?:LQ|NĐ|TT)(?:\.\d+)+)"
    r"\.",
    re.IGNORECASE,
)
CLAUSE_RE = re.compile(r"^\s*(?P<number>\d+)\.\s+(?P<body>.+)$", re.DOTALL)
POINT_RE = re.compile(
    r"^\s*(?P<label>[a-zđ])\)\s+(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL,
)
ATTACHMENT_RE = re.compile(
    r"\.(?:docx?|pdf|xlsx?|xls|zip|rar)(?:$|[?#])",
    re.IGNORECASE,
)

KNOWN_CLASSES = ("pChuong", "pDieu", "pGhiChu", "pNoiDung", "pChiDan")
ARTICLE_BOUNDARY_CLASSES = {"pDieu", "pChuong"}


def normalize_text(value: str) -> str:
    """Chuẩn hóa Unicode NFC và khoảng trắng, không xóa dấu tiếng Việt."""
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\xa0", " ")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def raw_tag_text(tag: Tag) -> str:
    """
    Lưu text gần với HTML nguồn trước khi gộp khoảng trắng.
    Vẫn chuẩn hóa Unicode và kiểu xuống dòng để JSON ổn định.
    """
    value = tag.get_text("\n", strip=False)
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\xa0", " ")
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def tag_text(tag: Tag) -> str:
    return normalize_text(tag.get_text(" ", strip=True))


def tag_classes(tag: Tag) -> set[str]:
    value = tag.get("class", [])
    if isinstance(value, str):
        return {value}
    return {str(item) for item in value}


def anchor_name(tag: Tag) -> str | None:
    anchor = tag.find("a", attrs={"name": True})
    if not isinstance(anchor, Tag):
        return None
    return normalize_text(str(anchor.get("name", ""))) or None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def count_classes(soup: BeautifulSoup) -> Counter[str]:
    counts: Counter[str] = Counter()
    for tag in soup.find_all(True):
        if not isinstance(tag, Tag):
            continue
        for class_name in tag_classes(tag):
            counts[class_name] += 1
    return counts


def detect_topic_metadata(
    soup: BeautifulSoup,
    html_path: Path,
    topic_code_override: str | None = None,
    topic_name_override: str | None = None,
    document_id_override: str | None = None,
) -> dict[str, str]:
    """Tự nhận diện mã và tên đề mục; CLI có quyền ghi đè."""
    detected_code: str | None = None
    detected_name: str | None = None
    candidates: list[str] = []

    for tag_name in ("h1", "h2", "h3", "h4", "title"):
        for tag in soup.find_all(tag_name):
            if isinstance(tag, Tag):
                text = tag_text(tag)
                if text:
                    candidates.append(text)

    if isinstance(soup.body, Tag):
        body_lines = normalize_text(
            soup.body.get_text("\n", strip=True)
        ).splitlines()
        candidates.extend(body_lines[:50])

    for candidate in candidates:
        match = TOPIC_HEADING_RE.search(candidate)
        if match:
            detected_code = normalize_text(match.group("topic_code"))
            detected_name = normalize_text(match.group("topic_name"))
            break

    # Dự phòng: lấy mã đề mục từ tiêu đề điều đầu tiên.
    first_article = soup.find(class_="pDieu")
    if not detected_code and isinstance(first_article, Tag):
        article_match = ARTICLE_HEADING_RE.match(tag_text(first_article))
        if article_match:
            article_code = article_match.group("article_code")
            detected_code = ".".join(article_code.split(".")[:2])

    topic_code = normalize_text(topic_code_override or detected_code or "")
    topic_name = normalize_text(topic_name_override or detected_name or "")

    if not topic_code:
        raise ValueError(
            "Không nhận diện được mã đề mục. Hãy truyền --topic-code."
        )
    if not topic_name:
        raise ValueError(
            "Không nhận diện được tên đề mục. Hãy truyền --topic-name."
        )

    document_id = normalize_text(
        document_id_override or f"phap-dien:{topic_code}"
    )

    return {
        "document_id": document_id,
        "topic_code": topic_code,
        "topic_name": topic_name,
        "source_file": str(html_path),
        "source_sha256": file_sha256(html_path),
        "parser_version": PARSER_VERSION,
    }


def parse_structure_and_contexts(
    soup: BeautifulSoup,
    topic_code: str,
) -> tuple[
    dict[str, Any],
    dict[int, dict[str, dict[str, Any] | None]],
    list[dict[str, Any]],
]:
    """
    Duyệt tài liệu theo thứ tự để:
    - ghép cặp marker/tên của Chương và Mục;
    - gắn chapter/section hiện hành cho từng pDieu;
    - thu thập pChiDan cấp Chương/Mục, không gán nhầm cho Điều.
    """
    items: list[dict[str, Any]] = []
    article_contexts: dict[int, dict[str, dict[str, Any] | None]] = {}
    structure_citations: list[dict[str, Any]] = []

    current_chapter: dict[str, Any] | None = None
    current_section: dict[str, Any] | None = None
    pending_structure: dict[str, Any] | None = None
    current_article: Tag | None = None

    for tag in soup.find_all(True):
        if not isinstance(tag, Tag):
            continue

        classes = tag_classes(tag)

        if "pChuong" in classes:
            current_article = None
            text = tag_text(tag)
            marker_match = STRUCTURE_MARKER_RE.match(text)

            if marker_match:
                kind = marker_match.group("kind").casefold()
                item = {
                    "kind": "chương" if kind == "chương" else "mục",
                    "number": normalize_text(marker_match.group("number")),
                    "title": None,
                    "anchor_id": anchor_name(tag),
                }
                items.append(item)
                pending_structure = item

                if item["kind"] == "chương":
                    current_chapter = item
                    current_section = None
                else:
                    current_section = item
                continue

            if pending_structure is not None and pending_structure["title"] is None:
                pending_structure["title"] = text or None
                pending_structure = None
            elif text:
                items.append(
                    {
                        "kind": "unclassified_structure_text",
                        "number": None,
                        "title": text,
                        "anchor_id": anchor_name(tag),
                    }
                )
            continue

        if "pDieu" in classes:
            current_article = tag
            article_contexts[id(tag)] = {
                "chapter": deepcopy(current_chapter),
                "section": deepcopy(current_section),
            }
            continue

        if "pChiDan" in classes and current_article is None:
            owner = current_section or current_chapter
            structure_citations.append(
                {
                    "owner": deepcopy(owner),
                    "citation": parse_citation_paragraph(tag, topic_code),
                }
            )

    return (
        {
            "chapter_count": sum(item["kind"] == "chương" for item in items),
            "section_count": sum(item["kind"] == "mục" for item in items),
            "items": items,
        },
        article_contexts,
        structure_citations,
    )


def extract_target_code(text: str) -> str | None:
    match = TARGET_CODE_RE.search(text)
    return match.group("target_code").upper() if match else None


def parse_citation_paragraph(
    tag: Tag,
    topic_code: str,
) -> dict[str, Any]:
    links: list[dict[str, Any]] = []

    for link in tag.find_all("a"):
        if not isinstance(link, Tag):
            continue

        onclick = str(link.get("onclick", ""))
        href_value = link.get("href")
        href = str(href_value) if href_value is not None else None
        match = INTERNAL_TARGET_RE.search(onclick)
        text = tag_text(link)
        target_code = extract_target_code(text)

        if match:
            relation_type = "internal_phap_dien"
            target_id = match.group("target_id")
        elif href and href != "#":
            relation_type = "external_legal_source"
            target_id = None
        else:
            relation_type = "unknown"
            target_id = None

        same_topic = bool(target_code and target_code.startswith(f"{topic_code}."))

        links.append(
            {
                "text": text,
                "relation_type": relation_type,
                "target_id": target_id,
                "target_code": target_code,
                "href": href,
                "same_topic": same_topic,
            }
        )

    return {
        "raw_text": raw_tag_text(tag),
        "text": tag_text(tag),
        "links": links,
    }


def source_document_id_from_urls(urls: Iterable[str]) -> str | None:
    for url in urls:
        query = parse_qs(urlparse(url).query)
        item_ids = query.get("ItemID") or query.get("itemid")
        if item_ids and item_ids[0]:
            return f"vbpl:item:{item_ids[0]}"
    return None


def parse_source_note(tag: Tag) -> dict[str, Any]:
    links: list[dict[str, str | None]] = []
    urls: list[str] = []

    for link in tag.find_all("a", href=True):
        if not isinstance(link, Tag):
            continue
        href = str(link.get("href"))
        links.append({"text": tag_text(link), "href": href})
        urls.append(href)

    # Giữ thứ tự nhưng loại URL trùng.
    unique_urls = list(dict.fromkeys(urls))

    return {
        "raw_text": raw_tag_text(tag),
        "text": tag_text(tag),
        "links": links,
        "urls": unique_urls,
        "source_document_id": source_document_id_from_urls(unique_urls),
    }


def is_attachment_link(tag: Tag) -> bool:
    if tag.name != "a":
        return False
    href = str(tag.get("href", ""))
    return bool(href and ATTACHMENT_RE.search(href))


def parse_attachment(tag: Tag, article_id: str | None, index: int) -> dict[str, Any]:
    href = str(tag.get("href", ""))
    filename = Path(urlparse(href).path).name or None
    extension = Path(filename).suffix.lstrip(".").lower() if filename else None

    return {
        "attachment_id": (
            f"{article_id}|attachment={index}" if article_id else f"attachment={index}"
        ),
        "raw_text": raw_tag_text(tag),
        "text": tag_text(tag),
        "href": href,
        "filename": filename,
        "extension": extension or None,
        "downloaded": False,
    }


def parse_table(tag: Tag, article_id: str | None, index: int) -> dict[str, Any]:
    rows: list[list[str]] = []
    header_flags: list[bool] = []

    for row_tag in tag.find_all("tr"):
        if not isinstance(row_tag, Tag):
            continue
        cells = [
            normalize_text(cell.get_text(" ", strip=True))
            for cell in row_tag.find_all(["th", "td"], recursive=False)
            if isinstance(cell, Tag)
        ]
        if not cells:
            continue
        rows.append(cells)
        header_flags.append(bool(row_tag.find("th", recursive=False)))

    headers: list[str] = []
    data_rows = rows
    if rows and header_flags and header_flags[0]:
        headers = rows[0]
        data_rows = rows[1:]

    text_lines: list[str] = []
    if headers:
        text_lines.append(" | ".join(headers))
    text_lines.extend(" | ".join(row) for row in data_rows)

    return {
        "table_id": f"{article_id}|table={index}" if article_id else f"table={index}",
        "headers": headers,
        "rows": data_rows,
        "text": "\n".join(text_lines) or tag_text(tag),
        "raw_text": raw_tag_text(tag),
        "row_count": len(data_rows),
        "column_count": max((len(row) for row in rows), default=0),
    }


def parse_content_block(
    tag: Tag,
    article_id: str | None,
    table_index: int,
    attachment_index: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """
    Trả về (content_block, table, attachment).
    """
    classes = sorted(tag_classes(tag))
    text = tag_text(tag)
    raw_text = raw_tag_text(tag)

    if "pNoiDung" in classes:
        return (
            {
                "kind": "content_marker",
                "tag": tag.name,
                "classes": classes,
                "raw_text": raw_text,
                "text": text,
                "is_empty": not bool(text),
            },
            None,
            None,
        )

    if tag.name == "table":
        table = parse_table(tag, article_id, table_index)
        return (
            {
                "kind": "table",
                "tag": tag.name,
                "classes": classes,
                "raw_text": raw_text,
                "text": table["text"],
                "table_id": table["table_id"],
            },
            table,
            None,
        )

    if is_attachment_link(tag):
        attachment = parse_attachment(tag, article_id, attachment_index)
        return (
            {
                "kind": "attachment",
                "tag": tag.name,
                "classes": classes,
                "raw_text": raw_text,
                "text": text,
                "attachment_id": attachment["attachment_id"],
                "href": attachment["href"],
            },
            None,
            attachment,
        )

    if not text:
        return None, None, None

    return (
        {
            "kind": "text",
            "tag": tag.name,
            "classes": classes,
            "raw_text": raw_text,
            "text": text,
        },
        None,
        None,
    )


def article_siblings(article_tag: Tag) -> list[Tag]:
    result: list[Tag] = []
    for sibling in article_tag.find_next_siblings():
        if not isinstance(sibling, Tag):
            continue
        if tag_classes(sibling) & ARTICLE_BOUNDARY_CLASSES:
            break
        result.append(sibling)
    return result


def build_content_units(
    article_id: str | None,
    content_blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Nhận diện cấu trúc pháp lý cơ bản từ các block:
    preamble → clause → point → clause_continuation → table.

    Một số điều pháp điển có thể lặp lại số khoản/điểm do nội dung sửa đổi,
    bổ sung hoặc nhiều cụm quy định được ghép chung. Vì vậy unit_id được
    làm duy nhất bằng hậu tố occurrence, nhưng vẫn giữ nguyên số khoản/điểm.
    """
    units: list[dict[str, Any]] = []
    warnings: list[str] = []
    current_clause: str | None = None
    preamble_index = 0
    continuation_index = 0
    id_occurrences: Counter[str] = Counter()

    def unique_unit_id(base_id: str) -> tuple[str, int]:
        id_occurrences[base_id] += 1
        occurrence = id_occurrences[base_id]
        if occurrence == 1:
            return base_id, occurrence
        return f"{base_id}|occurrence={occurrence}", occurrence

    for block in content_blocks:
        kind = block["kind"]

        if kind in {"content_marker", "attachment"}:
            continue

        if kind == "table":
            base_id = str(block["table_id"])
            unit_id, occurrence = unique_unit_id(base_id)
            units.append(
                {
                    "unit_id": unit_id,
                    "unit_occurrence": occurrence,
                    "unit_type": "table",
                    "clause_number": current_clause,
                    "point_label": None,
                    "raw_text": block["raw_text"],
                    "text": block["text"],
                }
            )
            continue

        text = block["text"]
        clause_match = CLAUSE_RE.match(text)
        point_match = POINT_RE.match(text)

        if clause_match:
            current_clause = clause_match.group("number")
            continuation_index = 0
            base_id = (
                f"{article_id}|clause={current_clause}"
                if article_id
                else f"clause={current_clause}"
            )
            unit_id, occurrence = unique_unit_id(base_id)
            if occurrence > 1:
                warnings.append(
                    f"Khoản {current_clause} xuất hiện lặp lần {occurrence}; "
                    "đã thêm occurrence vào unit_id."
                )
            units.append(
                {
                    "unit_id": unit_id,
                    "unit_occurrence": occurrence,
                    "unit_type": "clause",
                    "clause_number": current_clause,
                    "point_label": None,
                    "raw_text": block["raw_text"],
                    "text": text,
                }
            )
            continue

        if point_match:
            label = point_match.group("label").casefold()
            if current_clause is None:
                warnings.append(
                    f"Điểm {label}) xuất hiện trước khi nhận diện được khoản."
                )
            base_id = (
                f"{article_id}|clause={current_clause or 'unknown'}|point={label}"
                if article_id
                else f"clause={current_clause or 'unknown'}|point={label}"
            )
            unit_id, occurrence = unique_unit_id(base_id)
            if occurrence > 1:
                warnings.append(
                    f"Điểm {label}) của khoản {current_clause or 'không xác định'} "
                    f"xuất hiện lặp lần {occurrence}; đã thêm occurrence vào unit_id."
                )
            units.append(
                {
                    "unit_id": unit_id,
                    "unit_occurrence": occurrence,
                    "unit_type": "point",
                    "clause_number": current_clause,
                    "point_label": label,
                    "raw_text": block["raw_text"],
                    "text": text,
                }
            )
            continue

        if current_clause is None:
            preamble_index += 1
            base_id = (
                f"{article_id}|preamble={preamble_index}"
                if article_id
                else f"preamble={preamble_index}"
            )
            unit_type = "preamble"
        else:
            continuation_index += 1
            base_id = (
                f"{article_id}|clause={current_clause}|continuation={continuation_index}"
                if article_id
                else f"clause={current_clause}|continuation={continuation_index}"
            )
            unit_type = "clause_continuation"

        unit_id, occurrence = unique_unit_id(base_id)
        units.append(
            {
                "unit_id": unit_id,
                "unit_occurrence": occurrence,
                "unit_type": unit_type,
                "clause_number": current_clause,
                "point_label": None,
                "raw_text": block["raw_text"],
                "text": text,
            }
        )

    return units, list(dict.fromkeys(warnings))


def parse_article(
    article_tag: Tag,
    index: int,
    context: dict[str, dict[str, Any] | None],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    heading = tag_text(article_tag)
    heading_match = ARTICLE_HEADING_RE.match(heading)

    if not heading_match:
        raise ValueError(f"Không nhận diện được tiêu đề điều: {heading}")

    article_id = anchor_name(article_tag)
    source_note: dict[str, Any] | None = None
    citations: list[dict[str, Any]] = []
    content_blocks: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    warnings: list[str] = []

    marker_seen = False

    for sibling in article_siblings(article_tag):
        classes = tag_classes(sibling)

        if "pGhiChu" in classes:
            source_note = parse_source_note(sibling)
            continue

        if "pChiDan" in classes:
            citations.append(
                parse_citation_paragraph(
                    sibling,
                    str(provenance["topic_code"]),
                )
            )
            continue

        table_index = len(tables) + 1
        attachment_index = len(attachments) + 1
        block, table, attachment = parse_content_block(
            sibling,
            article_id,
            table_index,
            attachment_index,
        )

        if block is None:
            continue

        if block["kind"] == "content_marker":
            marker_seen = True
        elif not marker_seen:
            warnings.append(
                f"Block nội dung xuất hiện trước marker pNoiDung: {block['kind']}."
            )

        content_blocks.append(block)
        if table is not None:
            tables.append(table)
        if attachment is not None:
            attachments.append(attachment)

    content_texts = [
        block["text"]
        for block in content_blocks
        if block["kind"] in {"text", "table"} and block["text"]
    ]
    content_raw_texts = [
        block["raw_text"]
        for block in content_blocks
        if block["kind"] in {"text", "table"} and block["raw_text"]
    ]

    content_units, unit_warnings = build_content_units(article_id, content_blocks)
    warnings.extend(unit_warnings)

    article_code = heading_match.group("article_code").upper()
    topic_code = ".".join(article_code.split(".")[:2])
    expected_topic_code = str(provenance["topic_code"])

    if topic_code != expected_topic_code:
        raise ValueError(
            "Mã đề mục trong điều không khớp metadata file: "
            f"{article_code} thuộc {topic_code}, file là {expected_topic_code}."
        )

    if not marker_seen:
        warnings.append("Không tìm thấy marker pNoiDung.")
    if source_note is None:
        warnings.append("Không tìm thấy pGhiChu.")
    if not content_texts:
        warnings.append("Nội dung điều rỗng.")
    if context.get("chapter") is None:
        warnings.append("Không xác định được chương.")

    return {
        **provenance,
        "index": index,
        "article_id": article_id,
        "article_code": article_code,
        "codification_code": article_code,
        "topic_code": topic_code,
        "source_type": heading_match.group("source_type").upper(),
        "article_title": normalize_text(heading_match.group("title")),
        "heading": heading,
        "chapter": context.get("chapter"),
        "section": context.get("section"),
        "source_note": source_note,
        "source_note_text": source_note["text"] if source_note else None,
        "source_urls": source_note["urls"] if source_note else [],
        "source_document_id": (
            source_note["source_document_id"] if source_note else None
        ),
        "content_blocks": content_blocks,
        "content_units": content_units,
        "content_text_raw": "\n".join(content_raw_texts),
        "content_text": "\n".join(content_texts),
        "tables": tables,
        "attachments": attachments,
        "citations": citations,
        "relations": [],
        "warnings": list(dict.fromkeys(warnings)),
    }


def relation_key(relation: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(relation.get("source_id") or ""),
        str(relation.get("target_id") or relation.get("href") or relation.get("text") or ""),
        str(relation.get("relation_type") or ""),
    )


def flatten_article_relations(
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    article_ids = {
        str(article["article_id"])
        for article in articles
        if article.get("article_id") is not None
    }
    all_relations: list[dict[str, Any]] = []

    for article in articles:
        deduped: dict[tuple[str, str, str], dict[str, Any]] = {}

        for paragraph in article["citations"]:
            for link in paragraph["links"]:
                if link["relation_type"] == "internal_phap_dien":
                    normalized_relation_type = "RELATED_TO"
                elif link["relation_type"] == "external_legal_source":
                    normalized_relation_type = "EXTERNAL_REFERENCE"
                else:
                    normalized_relation_type = "UNKNOWN"

                relation = {
                    "source_scope": "article",
                    "source_id": article["article_id"],
                    "source_code": article["article_code"],
                    "target_id": link["target_id"],
                    "target_code": link["target_code"],
                    "relation_type": normalized_relation_type,
                    "target_in_corpus": bool(
                        link["target_id"] and link["target_id"] in article_ids
                    ),
                    "same_topic": link["same_topic"],
                    "href": link["href"],
                    "text": link["text"],
                    "citation_paragraph_text": paragraph["text"],
                    "mention_count": 1,
                }
                key = relation_key(relation)

                if key in deduped:
                    deduped[key]["mention_count"] += 1
                    existing = deduped[key].setdefault(
                        "citation_paragraph_texts",
                        [deduped[key]["citation_paragraph_text"]],
                    )
                    if paragraph["text"] not in existing:
                        existing.append(paragraph["text"])
                else:
                    deduped[key] = relation

        article["relations"] = list(deduped.values())
        all_relations.extend(article["relations"])

    return all_relations


def flatten_structure_relations(
    structure_citations: list[dict[str, Any]],
    article_ids: set[str],
) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}

    for item in structure_citations:
        owner = item["owner"]
        citation = item["citation"]

        if owner is None:
            source_id = None
            source_code = None
            source_scope = "unclassified_structure"
        else:
            source_id = owner.get("anchor_id")
            source_code = f"{owner.get('kind')} {owner.get('number')}"
            source_scope = owner.get("kind")

        for link in citation["links"]:
            if link["relation_type"] == "internal_phap_dien":
                normalized_relation_type = "RELATED_TO"
            elif link["relation_type"] == "external_legal_source":
                normalized_relation_type = "EXTERNAL_REFERENCE"
            else:
                normalized_relation_type = "UNKNOWN"

            relation = {
                "source_scope": source_scope,
                "source_id": source_id,
                "source_code": source_code,
                "target_id": link["target_id"],
                "target_code": link["target_code"],
                "relation_type": normalized_relation_type,
                "target_in_corpus": bool(
                    link["target_id"] and link["target_id"] in article_ids
                ),
                "same_topic": link["same_topic"],
                "href": link["href"],
                "text": link["text"],
                "citation_paragraph_text": citation["text"],
                "mention_count": 1,
            }
            key = relation_key(relation)
            if key in deduped:
                deduped[key]["mention_count"] += 1
            else:
                deduped[key] = relation

    return list(deduped.values())


def validate_corpus(
    articles: list[dict[str, Any]],
    all_relations: list[dict[str, Any]],
    structure_relations: list[dict[str, Any]],
    class_counts: Counter[str],
    topic_code: str,
    expected_article_count: int | None = None,
    expected_source_counts: dict[str, int] | None = None,
    expected_table_count: int | None = None,
    expected_attachment_count: int | None = None,
) -> dict[str, Any]:
    article_ids = [article.get("article_id") for article in articles]
    valid_ids = [str(value) for value in article_ids if value is not None]
    source_counts = Counter(article["source_type"] for article in articles)

    table_count = sum(len(article["tables"]) for article in articles)
    attachment_count = sum(len(article["attachments"]) for article in articles)
    empty_articles = [
        article["article_code"]
        for article in articles
        if not article["content_text"].strip()
    ]
    without_chapter = [
        article["article_code"]
        for article in articles
        if article["chapter"] is None
    ]
    without_source_note = [
        article["article_code"]
        for article in articles
        if article["source_note"] is None
    ]

    duplicate_ids = [
        value
        for value, count in Counter(valid_ids).items()
        if count > 1
    ]

    all_unit_ids = [
        str(unit["unit_id"])
        for article in articles
        for unit in article["content_units"]
    ]
    duplicate_unit_ids = [
        value
        for value, count in Counter(all_unit_ids).items()
        if count > 1
    ]
    repeated_unit_occurrence_count = sum(
        1
        for article in articles
        for unit in article["content_units"]
        if int(unit.get("unit_occurrence", 1)) > 1
    )

    unresolved_same_topic = [
        relation
        for relation in [*all_relations, *structure_relations]
        if relation["relation_type"] == "RELATED_TO"
        and relation["same_topic"]
        and not relation["target_in_corpus"]
    ]

    p_noi_dung_count = class_counts.get("pNoiDung", 0)
    empty_markers = sum(
        1
        for article in articles
        for block in article["content_blocks"]
        if block["kind"] == "content_marker" and block.get("is_empty")
    )

    errors: list[str] = []
    warnings: list[str] = []

    if (
        expected_article_count is not None
        and len(articles) != expected_article_count
    ):
        errors.append(
            f"Số điều {len(articles)} khác kỳ vọng {expected_article_count}."
        )
    if (
        expected_source_counts is not None
        and dict(source_counts) != expected_source_counts
    ):
        errors.append(
            f"Phân bố nguồn {dict(source_counts)} khác kỳ vọng "
            f"{expected_source_counts}."
        )
    if any(value is None for value in article_ids):
        errors.append("Có article_id bị thiếu.")
    if duplicate_ids:
        errors.append(f"Có article_id bị trùng: {len(duplicate_ids)}.")
    if duplicate_unit_ids:
        errors.append(f"Có unit_id bị trùng: {len(duplicate_unit_ids)}.")
    if empty_articles:
        errors.append(f"Có điều rỗng: {len(empty_articles)}.")
    if without_chapter:
        errors.append(f"Có điều không xác định được chương: {len(without_chapter)}.")
    if (
        expected_table_count is not None
        and table_count != expected_table_count
    ):
        errors.append(
            f"Số bảng {table_count} khác kỳ vọng {expected_table_count}."
        )
    if (
        expected_attachment_count is not None
        and attachment_count != expected_attachment_count
    ):
        errors.append(
            f"Số attachment {attachment_count} khác kỳ vọng "
            f"{expected_attachment_count}."
        )
    if unresolved_same_topic:
        errors.append(
            f"Có quan hệ cùng Đề mục {topic_code} nhưng không tìm thấy node đích: "
            f"{len(unresolved_same_topic)}."
        )
    if without_source_note:
        warnings.append(f"Có điều thiếu ghi chú nguồn: {len(without_source_note)}.")
    if empty_markers != p_noi_dung_count:
        warnings.append(
            f"Marker pNoiDung rỗng {empty_markers}/{p_noi_dung_count}; "
            "cần kiểm tra các marker có text."
        )

    return {
        "is_valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "article_count": len(articles),
        "source_counts": dict(source_counts),
        "missing_article_id_count": sum(value is None for value in article_ids),
        "duplicate_article_id_count": len(duplicate_ids),
        "duplicate_article_ids": duplicate_ids,
        "duplicate_unit_id_count": len(duplicate_unit_ids),
        "duplicate_unit_ids": duplicate_unit_ids,
        "repeated_unit_occurrence_count": repeated_unit_occurrence_count,
        "empty_article_count": len(empty_articles),
        "empty_articles": empty_articles,
        "articles_without_chapter_count": len(without_chapter),
        "articles_without_chapter": without_chapter,
        "articles_without_source_note_count": len(without_source_note),
        "table_count": table_count,
        "attachment_count": attachment_count,
        "p_noi_dung_count": p_noi_dung_count,
        "empty_p_noi_dung_count": empty_markers,
        "article_relation_count": len(all_relations),
        "structure_relation_count": len(structure_relations),
        "same_topic_unresolved_relation_count": len(unresolved_same_topic),
    }


def inspect_document(
    html_path: Path,
    sample_limit: int,
    topic_code_override: str | None = None,
    topic_name_override: str | None = None,
    document_id_override: str | None = None,
    expected_article_count: int | None = None,
    expected_source_counts: dict[str, int] | None = None,
    expected_table_count: int | None = None,
    expected_attachment_count: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw_html = html_path.read_bytes()
    soup = BeautifulSoup(raw_html, "lxml")
    class_counts = count_classes(soup)
    parsed_at = datetime.now(timezone.utc).isoformat()

    document_metadata = detect_topic_metadata(
        soup=soup,
        html_path=html_path,
        topic_code_override=topic_code_override,
        topic_name_override=topic_name_override,
        document_id_override=document_id_override,
    )

    provenance = {
        "document_id": document_metadata["document_id"],
        "source_file": document_metadata["source_file"],
        "source_sha256": document_metadata["source_sha256"],
        "parser_version": document_metadata["parser_version"],
        "topic_code": document_metadata["topic_code"],
        "topic_name": document_metadata["topic_name"],
    }

    structure, article_contexts, structure_citations = (
        parse_structure_and_contexts(
            soup,
            document_metadata["topic_code"],
        )
    )

    article_tags = [
        tag
        for tag in soup.find_all(class_="pDieu")
        if isinstance(tag, Tag)
    ]
    articles = [
        parse_article(
            tag,
            index,
            article_contexts.get(
                id(tag),
                {"chapter": None, "section": None},
            ),
            provenance,
        )
        for index, tag in enumerate(article_tags, start=1)
    ]

    article_relations = flatten_article_relations(articles)
    article_id_set = {
        str(article["article_id"])
        for article in articles
        if article["article_id"] is not None
    }
    structure_relations = flatten_structure_relations(
        structure_citations,
        article_id_set,
    )

    source_counts = Counter(article["source_type"] for article in articles)
    citation_paragraphs = [
        tag
        for tag in soup.find_all(class_="pChiDan")
        if isinstance(tag, Tag)
    ]
    citation_links = [
        link
        for paragraph in citation_paragraphs
        for link in parse_citation_paragraph(
            paragraph,
            document_metadata["topic_code"],
        )["links"]
    ]

    validation = validate_corpus(
        articles,
        article_relations,
        structure_relations,
        class_counts,
        topic_code=document_metadata["topic_code"],
        expected_article_count=expected_article_count,
        expected_source_counts=expected_source_counts,
        expected_table_count=expected_table_count,
        expected_attachment_count=expected_attachment_count,
    )

    metadata = {
        **document_metadata,
        "detected_encoding": soup.original_encoding,
        "parsed_at": parsed_at,
    }

    canonical = {
        "metadata": metadata,
        "structure": structure,
        "structure_relations": structure_relations,
        "articles": articles,
    }

    report = {
        "metadata": metadata,
        "class_counts": dict(class_counts),
        "structure": structure,
        "article_statistics": {
            "total": len(articles),
            "by_source_type": dict(source_counts),
            "missing_anchor_ids": validation["missing_article_id_count"],
            "duplicate_anchor_ids": validation["duplicate_article_id_count"],
            "empty_article_count": validation["empty_article_count"],
            "articles_without_chapter_count": (
                validation["articles_without_chapter_count"]
            ),
            "table_count": validation["table_count"],
            "attachment_count": validation["attachment_count"],
            "p_noi_dung_count": validation["p_noi_dung_count"],
            "empty_p_noi_dung_count": validation["empty_p_noi_dung_count"],
        },
        "citation_statistics": {
            "paragraph_count": len(citation_paragraphs),
            "article_paragraph_count": (
                len(citation_paragraphs) - len(structure_citations)
            ),
            "structure_paragraph_count": len(structure_citations),
            "internal_link_count": sum(
                link["relation_type"] == "internal_phap_dien"
                for link in citation_links
            ),
            "internal_same_topic": sum(
                link["relation_type"] == "internal_phap_dien"
                and link["same_topic"]
                for link in citation_links
            ),
            "internal_other_topic": sum(
                link["relation_type"] == "internal_phap_dien"
                and not link["same_topic"]
                for link in citation_links
            ),
            "external_link_count": sum(
                link["relation_type"] == "external_legal_source"
                for link in citation_links
            ),
            "article_relation_count_after_dedup": len(article_relations),
            "structure_relation_count_after_dedup": len(structure_relations),
        },
        "validation": validation,
        "sample_articles": articles[:sample_limit],
    }

    return report, canonical, validation


def markdown_escape(text: str | None) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    class_counts = report["class_counts"]
    structure = report["structure"]
    article_stats = report["article_statistics"]
    citation_stats = report["citation_statistics"]
    validation = report["validation"]

    lines = [
        f"# Cấu trúc HTML — Đề mục {metadata['topic_code']} "
        f"{metadata['topic_name']}",
        "",
        "## 1. File được khảo sát",
        "",
        f"- File: `{metadata['source_file']}`",
        f"- Encoding phát hiện: `{metadata['detected_encoding']}`",
        f"- SHA-256: `{metadata['source_sha256']}`",
        f"- Phiên bản parser: `{metadata['parser_version']}`",
        "- Nguồn là HTML có cấu trúc, vì vậy **không sử dụng OCR**.",
        "",
        "## 2. Thống kê tổng quan",
        "",
        f"- Tổng số điều: **{article_stats['total']}**",
        f"- Chương: **{structure['chapter_count']}**",
        f"- Mục: **{structure['section_count']}**",
        f"- Bảng nằm trong nội dung điều: **{article_stats['table_count']}**",
        f"- Tệp/phụ lục đính kèm: **{article_stats['attachment_count']}**",
        f"- Anchor điều bị thiếu: **{article_stats['missing_anchor_ids']}**",
        f"- Anchor điều bị trùng: **{article_stats['duplicate_anchor_ids']}**",
        f"- Điều rỗng: **{article_stats['empty_article_count']}**",
        f"- Điều không xác định được chương: "
        f"**{article_stats['articles_without_chapter_count']}**",
        "",
        "### Phân bố theo nguồn",
        "",
        "| Mã nguồn | Loại nguồn | Số điều |",
        "|---|---|---:|",
        f"| `LQ` | Bộ luật/Luật | "
        f"{article_stats['by_source_type'].get('LQ', 0)} |",
        f"| `NĐ` | Nghị định | "
        f"{article_stats['by_source_type'].get('NĐ', 0)} |",
        f"| `TT` | Thông tư | "
        f"{article_stats['by_source_type'].get('TT', 0)} |",
        "",
        "## 3. Các class HTML chính",
        "",
        "| Class | Số lượng | Vai trò thực tế |",
        "|---|---:|---|",
        f"| `pChuong` | {class_counts.get('pChuong', 0)} | "
        "Dùng chung cho mã chương, tên chương, mã mục và tên mục |",
        f"| `pDieu` | {class_counts.get('pDieu', 0)} | "
        "Tiêu đề điều pháp điển và anchor ID |",
        f"| `pGhiChu` | {class_counts.get('pGhiChu', 0)} | "
        "Ghi chú nguồn, số văn bản, hiệu lực và URL nguồn |",
        f"| `pNoiDung` | {class_counts.get('pNoiDung', 0)} | "
        f"Marker bắt đầu nội dung; rỗng "
        f"{article_stats['empty_p_noi_dung_count']}/"
        f"{article_stats['p_noi_dung_count']} thẻ |",
        f"| `pChiDan` | {class_counts.get('pChiDan', 0)} | "
        "Chỉ dẫn/quan hệ liên quan giữa điều, mục hoặc chương |",
        "",
        "## 4. Cấu trúc chương và mục",
        "",
        "File không có class riêng `pMuc`. Cả chương và mục đều dùng "
        "`pChuong` theo cặp marker–tiêu đề.",
        "",
        "Quy tắc nhận diện:",
        "",
        "- Text bắt đầu bằng `Chương` → marker chương.",
        "- Text bắt đầu bằng `Mục` → marker mục.",
        "- `pChuong` tiếp theo không có marker → tên của cấu trúc vừa gặp.",
        "- Khi bắt đầu Chương mới, Mục hiện hành được đặt lại về `null`.",
        "- Mỗi điều được gắn bản sao metadata Chương/Mục hiện hành.",
        "",
        "## 5. Ranh giới và nội dung điều",
        "",
        "- Bắt đầu tại `pDieu`.",
        "- Duyệt các sibling kế tiếp.",
        "- Dừng khi gặp `pDieu` hoặc `pChuong` mới.",
        "- `pNoiDung` là marker rỗng; nội dung thật nằm ở các sibling sau nó.",
        "- Giữ cả `content_text_raw` và `content_text` đã chuẩn hóa.",
        "- Nhận diện `content_units`: preamble, clause, point, "
        "clause_continuation và table.",
        "",
        "## 6. Mã pháp điển và ID",
        "",
        "- Giữ nguyên toàn bộ mã dưới trường `codification_code`; "
        "không suy diễn số điều gốc từ phần tử cuối của mã.",
        "- `article_id` lấy từ `<a name>` và luôn lưu dưới dạng chuỗi.",
        "- `source_sha256` và `parser_version` được lưu để tái lập thí nghiệm.",
        "",
        "## 7. Quan hệ chỉ dẫn",
        "",
        f"- Tổng đoạn `pChiDan`: **{citation_stats['paragraph_count']}**",
        f"- Gắn với điều: **{citation_stats['article_paragraph_count']}**",
        f"- Gắn với chương/mục: "
        f"**{citation_stats['structure_paragraph_count']}**",
        f"- Liên kết nội bộ: **{citation_stats['internal_link_count']}**",
        f"- Cùng Đề mục {metadata['topic_code']}: "
        f"**{citation_stats['internal_same_topic']}**",
        f"- Sang đề mục khác: **{citation_stats['internal_other_topic']}**",
        f"- Liên kết ngoài: **{citation_stats['external_link_count']}**",
        f"- Quan hệ cấp điều sau loại trùng: "
        f"**{citation_stats['article_relation_count_after_dedup']}**",
        f"- Quan hệ cấp cấu trúc sau loại trùng: "
        f"**{citation_stats['structure_relation_count_after_dedup']}**",
        "",
        "Quan hệ được làm phẳng thành `RELATED_TO`, "
        "`EXTERNAL_REFERENCE` hoặc `UNKNOWN`. `target_in_corpus` được xác định "
        "bằng tập `article_id` thực tế, không chỉ dựa vào mã đề mục.",
        "",
        "## 8. Bảng và attachment",
        "",
        "- Bảng được lưu thành `headers`, `rows` và `text`.",
        "- Attachment lưu tên, URL, phần mở rộng và `downloaded = false`.",
        "- Không tải, OCR hoặc nhúng nội dung attachment trong giai đoạn này.",
        "",
        "## 9. Kết quả parse thử",
        "",
        "| STT | Mã điều | Loại | Tên điều | Chương | Mục | "
        "Số unit | Chỉ dẫn |",
        "|---:|---|---|---|---|---|---:|---:|",
    ]

    for article in report["sample_articles"]:
        chapter = article.get("chapter") or {}
        section = article.get("section") or {}
        lines.append(
            "| {index} | `{code}` | `{source}` | {title} | {chapter} | "
            "{section} | {units} | {relations} |".format(
                index=article["index"],
                code=article["article_code"],
                source=article["source_type"],
                title=markdown_escape(article["article_title"]),
                chapter=markdown_escape(chapter.get("number")),
                section=markdown_escape(section.get("number")),
                units=len(article["content_units"]),
                relations=len(article["relations"]),
            )
        )

    lines.extend(
        [
            "",
            "## 10. Validation",
            "",
            f"- Trạng thái: **{'ĐẠT' if validation['is_valid'] else 'CHƯA ĐẠT'}**",
            f"- Lỗi: **{len(validation['errors'])}**",
            f"- Cảnh báo: **{len(validation['warnings'])}**",
        ]
    )

    if validation["errors"]:
        lines.extend(["", "### Lỗi"])
        lines.extend(f"- {item}" for item in validation["errors"])

    if validation["warnings"]:
        lines.extend(["", "### Cảnh báo"])
        lines.extend(f"- {item}" for item in validation["warnings"])

    lines.extend(
        [
            "",
            "## 11. Kết luận",
            "",
            "Dữ liệu đủ nhất quán để khóa làm corpus canonical trước khi "
            "thực hiện structural parent–child chunking. Parser bảo toàn cấu "
            "trúc Chương/Mục/Điều, nội dung thô và chuẩn hóa, khoản/điểm, bảng, "
            "attachment, provenance và quan hệ graph.",
        ]
    )

    return "\n".join(lines) + "\n"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Khảo sát và chuẩn hóa HTML một đề mục trong Bộ pháp điển."
        )
    )
    parser.add_argument("html_path", type=Path, help="Đường dẫn file HTML")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Số điều đưa vào báo cáo mẫu",
    )
    parser.add_argument(
        "--topic-code",
        type=str,
        default=None,
        help="Ghi đè mã đề mục nếu HTML không nhận diện được",
    )
    parser.add_argument(
        "--topic-name",
        type=str,
        default=None,
        help="Ghi đè tên đề mục nếu HTML không nhận diện được",
    )
    parser.add_argument(
        "--document-id",
        type=str,
        default=None,
        help="Ghi đè document ID; mặc định phap-dien:<topic_code>",
    )
    parser.add_argument(
        "--expected-article-count",
        type=int,
        default=None,
        help="Số điều kỳ vọng để validation nghiêm ngặt",
    )
    parser.add_argument(
        "--expected-source-counts",
        type=str,
        default=None,
        help='JSON số lượng nguồn, ví dụ {"LQ":220,"NĐ":174,"TT":83}',
    )
    parser.add_argument(
        "--expected-table-count",
        type=int,
        default=None,
        help="Số bảng kỳ vọng",
    )
    parser.add_argument(
        "--expected-attachment-count",
        type=int,
        default=None,
        help="Số attachment kỳ vọng",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("data/processed/html_inspection.json"),
        help="Báo cáo khảo sát dạng JSON",
    )
    parser.add_argument(
        "--md-output",
        type=Path,
        default=Path("docs/design/html_structure.md"),
        help="Tài liệu cấu trúc HTML",
    )
    parser.add_argument(
        "--articles-output",
        type=Path,
        default=Path("data/processed/articles_raw.json"),
        help="Toàn bộ corpus canonical gồm 477 điều",
    )
    parser.add_argument(
        "--validation-output",
        type=Path,
        default=Path("data/processed/parser_report.json"),
        help="Báo cáo validation riêng",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Trả mã lỗi nếu validation không đạt",
    )
    return parser.parse_args()


def parse_expected_source_counts(value: str | None) -> dict[str, int] | None:
    if value is None:
        return None

    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "--expected-source-counts phải là JSON hợp lệ."
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError("--expected-source-counts phải là một JSON object.")

    result: dict[str, int] = {}
    for key, count in payload.items():
        if not isinstance(key, str) or not isinstance(count, int):
            raise ValueError(
                "--expected-source-counts phải có dạng mã nguồn -> số nguyên."
            )
        result[key.upper()] = count
    return result


def main() -> None:
    args = parse_args()

    if not args.html_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file HTML: {args.html_path}")
    if args.limit < 1:
        raise ValueError("--limit phải lớn hơn hoặc bằng 1")

    report, canonical, validation = inspect_document(
        html_path=args.html_path,
        sample_limit=args.limit,
        topic_code_override=args.topic_code,
        topic_name_override=args.topic_name,
        document_id_override=args.document_id,
        expected_article_count=args.expected_article_count,
        expected_source_counts=parse_expected_source_counts(
            args.expected_source_counts
        ),
        expected_table_count=args.expected_table_count,
        expected_attachment_count=args.expected_attachment_count,
    )

    write_json(args.json_output, report)
    write_json(args.articles_output, canonical)
    write_json(args.validation_output, validation)

    args.md_output.parent.mkdir(parents=True, exist_ok=True)
    args.md_output.write_text(
        render_markdown(report),
        encoding="utf-8",
    )

    stats = report["article_statistics"]
    citations = report["citation_statistics"]

    print("HTML inspection and canonical parsing completed")
    print(f"File: {args.html_path}")
    print(
        f"Topic: {report['metadata']['topic_code']} - "
        f"{report['metadata']['topic_name']}"
    )
    print(f"Document ID: {report['metadata']['document_id']}")
    print(f"Parser version: {report['metadata']['parser_version']}")
    print(f"Articles: {stats['total']}")
    print(f"Sources: {stats['by_source_type']}")
    print(f"Chapters: {report['structure']['chapter_count']}")
    print(f"Sections: {report['structure']['section_count']}")
    print(f"Tables: {stats['table_count']}")
    print(f"Attachments: {stats['attachment_count']}")
    print(
        "Relations: "
        f"{citations['article_relation_count_after_dedup']} article + "
        f"{citations['structure_relation_count_after_dedup']} structure"
    )
    print(f"Validation: {'PASS' if validation['is_valid'] else 'FAIL'}")
    print(f"Inspection JSON: {args.json_output}")
    print(f"Canonical articles: {args.articles_output}")
    print(f"Validation report: {args.validation_output}")
    print(f"Markdown: {args.md_output}")

    if args.strict and not validation["is_valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()