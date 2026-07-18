"""Structural chunking theo Điều → Khoản → Điểm."""

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid5
from langchain_text_splitters import RecursiveCharacterTextSplitter

CHUNKER_VERSION = "1.0.0"

logger = logging.getLogger(__name__)

# WARNING: Do NOT change this namespace UUID after indexing to production.
# Changing it will invalidate all existing chunk_ids in the databases.
CHUNK_NAMESPACE = UUID("c3c8cf32-8418-4903-8d01-e28bb7c040d3")


class TokenCounter(Protocol):
    """Protocol for counting tokens in a given text."""

    name: str

    def count(self, text: str) -> int:
        """Count the number of tokens in the text."""
        ...


class TiktokenTokenCounter:
    """Token counter using tiktoken encoding."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        import tiktoken

        self._encoding_name = encoding_name
        self._encoder = tiktoken.get_encoding(encoding_name)
        self.name = f"tiktoken:{encoding_name}"

    def count(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("Input text must be a string")
        if not text:
            return 0
        return len(self._encoder.encode(text))


class RegexEstimatedTokenCounter:
    """Estimate token count using regex word counting for Vietnamese text.

    Note: This is an estimation, not an exact token count.
    """

    def __init__(self) -> None:
        self.name = "regex-estimate-v1"

    def count(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("Input text must be a string")
        if not text:
            return 0
        # Unicode-safe estimation: word count multiplied by 1.3
        words = re.findall(r"\w+", text, re.UNICODE)
        return int(len(words) * 1.3)


def get_default_token_counter() -> TokenCounter:
    """Factory to get the default token counter, falling back to regex estimate if tiktoken fails."""
    try:
        return TiktokenTokenCounter()
    except Exception as e:
        logger.warning(
            f"Failed to initialize TiktokenTokenCounter, falling back to RegexEstimatedTokenCounter: {e}"
        )
        return RegexEstimatedTokenCounter()


@dataclass(frozen=True)
class ChunkingConfig:
    """Configuration options for legal chunking."""

    target_tokens: int = 500
    max_tokens: int = 750
    fallback_overlap: int = 80
    chunker_version: str = CHUNKER_VERSION

    def __post_init__(self) -> None:
        # bool is instance of int in Python, so we must explicitly check type
        if type(self.target_tokens) is not int:
            raise TypeError("target_tokens must be an integer")
        if type(self.max_tokens) is not int:
            raise TypeError("max_tokens must be an integer")
        if type(self.fallback_overlap) is not int:
            raise TypeError("fallback_overlap must be an integer")
        if not isinstance(self.chunker_version, str):
            raise TypeError("chunker_version must be a string")

        if self.target_tokens <= 0:
            raise ValueError("target_tokens must be greater than 0")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be greater than 0")
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens must be less than or equal to max_tokens")
        if self.fallback_overlap < 0:
            raise ValueError("fallback_overlap must be non-negative")
        if self.fallback_overlap >= self.target_tokens:
            raise ValueError("fallback_overlap must be less than target_tokens")
        if not self.chunker_version:
            raise ValueError("chunker_version cannot be empty")


def make_chunk_id(chunk_key: str) -> str:
    """Generate a stable UUIDv5 string based on chunk_key."""
    if not isinstance(chunk_key, str):
        raise TypeError("chunk_key must be a string")
    if not chunk_key or chunk_key.isspace():
        raise ValueError("chunk_key cannot be empty or whitespace only")
    return str(uuid5(CHUNK_NAMESPACE, chunk_key))


def _validate_int(name: str, val: Any) -> None:
    if val is not None:
        if type(val) is not int:  # rejects bool
            raise TypeError(f"{name} must be an integer, got {type(val)}")
        if val < 1:
            raise ValueError(f"{name} must be >= 1, got {val}")


def build_chunk_key(
    *,
    article_id: str,
    chunk_type: str,
    clause_number: str | None = None,
    clause_occurrence: int | None = None,
    point_labels: list[str] | tuple[str, ...] | None = None,
    point_occurrences: list[int] | tuple[int, ...] | None = None,
    table_index: int | None = None,
    segment_index: int | None = None,
) -> str:
    """Build a deterministic and human-readable chunk key."""
    # Validation
    if not isinstance(article_id, str):
        raise TypeError("article_id must be a string")
    if not article_id:
        raise ValueError("article_id cannot be empty")

    if not isinstance(chunk_type, str):
        raise TypeError("chunk_type must be a string")

    whitelist = {"article", "preamble", "clause", "points", "fallback_segment", "table"}
    if chunk_type not in whitelist:
        raise ValueError(f"chunk_type must be one of {whitelist}")

    if clause_number is not None:
        if not isinstance(clause_number, str):
            raise TypeError("clause_number must be a string")
        if not clause_number:
            raise ValueError("clause_number cannot be empty")

    _validate_int("clause_occurrence", clause_occurrence)
    _validate_int("table_index", table_index)
    _validate_int("segment_index", segment_index)

    if point_labels is not None:
        if not isinstance(point_labels, (list, tuple)):
            raise TypeError("point_labels must be a list or tuple")
        for idx, label in enumerate(point_labels):
            if not isinstance(label, str):
                raise TypeError(f"point_label at index {idx} must be str")
            if not label:
                raise ValueError(f"point_label at index {idx} cannot be empty")

    if point_occurrences is not None:
        if not isinstance(point_occurrences, (list, tuple)):
            raise TypeError("point_occurrences must be a list or tuple")
        if point_labels is None or len(point_occurrences) != len(point_labels):
            raise ValueError("point_occurrences length must match point_labels length")
        for idx, occ in enumerate(point_occurrences):
            _validate_int(f"point_occurrence at index {idx}", occ)

    # Key construction
    parts = [article_id]
    if chunk_type == "article":
        parts.append("article")
    elif chunk_type == "preamble":
        parts.append("preamble")
    elif chunk_type == "clause":
        clause_str = f"clause={clause_number}"
        parts.append(clause_str)
        if clause_occurrence and clause_occurrence > 1:
            parts.append(f"occurrence={clause_occurrence}")
    elif chunk_type == "points":
        clause_str = f"clause={clause_number}"
        parts.append(clause_str)
        if clause_occurrence and clause_occurrence > 1:
            parts.append(f"occurrence={clause_occurrence}")
        if point_labels:
            has_multi_occ = False
            if point_occurrences:
                if any(occ > 1 for occ in point_occurrences):
                    has_multi_occ = True

            if has_multi_occ:
                parts_pts = []
                for l, occ in zip(point_labels, point_occurrences):
                    parts_pts.append(f"{l}@{occ}")
                points_str = ",".join(parts_pts)
            else:
                is_consecutive = True
                for i in range(1, len(point_labels)):
                    if len(point_labels[i]) != 1 or len(point_labels[i-1]) != 1:
                        is_consecutive = False
                        break
                    if ord(point_labels[i]) - ord(point_labels[i-1]) != 1:
                        is_consecutive = False
                        break
                if is_consecutive and len(point_labels) > 1:
                    points_str = f"{point_labels[0]}-{point_labels[-1]}"
                else:
                    points_str = ",".join(point_labels)
            parts.append(f"points={points_str}")
    elif chunk_type == "table":
        tbl_idx = table_index if table_index is not None else 1
        parts.append(f"table={tbl_idx}")
    elif chunk_type == "fallback_segment":
        if clause_number is not None:
            clause_str = f"clause={clause_number}"
            parts.append(clause_str)
            if clause_occurrence and clause_occurrence > 1:
                parts.append(f"occurrence={clause_occurrence}")
        if point_labels:
            has_multi_occ = False
            if point_occurrences:
                if any(occ > 1 for occ in point_occurrences):
                    has_multi_occ = True

            if has_multi_occ:
                parts_pts = []
                for l, occ in zip(point_labels, point_occurrences):
                    parts_pts.append(f"{l}@{occ}")
                points_str = ",".join(parts_pts)
            else:
                is_consecutive = True
                for i in range(1, len(point_labels)):
                    if len(point_labels[i]) != 1 or len(point_labels[i-1]) != 1:
                        is_consecutive = False
                        break
                    if ord(point_labels[i]) - ord(point_labels[i-1]) != 1:
                        is_consecutive = False
                        break
                if is_consecutive and len(point_labels) > 1:
                    points_str = f"{point_labels[0]}-{point_labels[-1]}"
                else:
                    points_str = ",".join(point_labels)
            parts.append(f"points={points_str}")

    if segment_index is not None:
        parts.append(f"segment={segment_index}")

    key = "|".join(parts)
    if "None" in key:
        raise ValueError("Generated key cannot contain the string 'None'")
    return key


def _group_text_units(article: dict) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Group article content units following state machine rules, excluding tables."""
    preamble_units = []
    clause_groups = []
    orphan_units = []
    warnings = []

    active_clause_group = None

    def find_last_clause_group(c_num: str):
        matching = [g for g in clause_groups if g["clause_number"] == c_num]
        if matching:
            return matching[-1]
        return None

    for unit in article.get("content_units", []):
        utype = unit.get("unit_type")
        uid = unit.get("unit_id")

        if utype == "table":
            # Excluded authoritatively from text grouping to avoid duplicate table text
            continue

        if utype == "preamble":
            preamble_units.append(unit)

        elif utype == "clause":
            active_clause_group = {
                "clause_number": unit.get("clause_number"),
                "clause_occurrence": unit.get("unit_occurrence", 1),
                "clause_unit": unit,
                "ordered_units": [unit],
                "point_labels": [],
                "point_occurrences": [],
                "source_unit_ids": [uid],
                "warnings": []
            }
            clause_groups.append(active_clause_group)

        elif utype == "clause_continuation":
            if active_clause_group:
                active_clause_group["ordered_units"].append(unit)
                active_clause_group["source_unit_ids"].append(uid)
            else:
                orphan_units.append(unit)
                warnings.append(f"orphan_clause_continuation: {uid}")

        elif utype == "point":
            p_clause_num = unit.get("clause_number")
            p_label = unit.get("point_label")
            p_occ = unit.get("unit_occurrence", 1)

            target_group = None
            if active_clause_group and active_clause_group["clause_number"] == p_clause_num:
                target_group = active_clause_group
            else:
                target_group = find_last_clause_group(p_clause_num)
                if not target_group and active_clause_group:
                    warnings.append(
                        f"point_clause_mismatch: {uid} (expected {p_clause_num}, active {active_clause_group['clause_number']})"
                    )

            if target_group:
                target_group["ordered_units"].append(unit)
                target_group["source_unit_ids"].append(uid)
                target_group["point_labels"].append(p_label)
                target_group["point_occurrences"].append(p_occ)
            else:
                orphan_units.append(unit)
                if not p_clause_num:
                    warnings.append(f"orphan_point: {uid}")
                else:
                    warnings.append(f"orphan_point: {uid} (clause {p_clause_num} not found)")

        else:
            orphan_units.append(unit)
            warnings.append(f"unsupported_unit_type: {utype} ({uid})")

    return preamble_units, clause_groups, orphan_units, warnings


def _build_body_text(units: list[dict]) -> str:
    """Nối text unit: strip khoảng trắng, bỏ unit rỗng, nối bằng newline."""
    texts = []
    for u in units:
        text = u.get("text")
        if text is None:
            text = u.get("raw_text")
        if text:
            stripped = text.strip()
            if stripped:
                texts.append(stripped)
    return "\n".join(texts)


def _build_chunk_content(
    article: dict,
    *,
    body_text: str,
    clause_number: str | None = None,
    point_labels: list[str] | None = None,
) -> str:
    """Dựng content với template breadcrumbs, không None, không URLs."""
    topic_code = article.get("topic_code")
    topic_name = article.get("topic_name")
    chapter = article.get("chapter")
    section = article.get("section")
    article_code = article.get("article_code")
    article_title = article.get("article_title")
    source_type = article.get("source_type")

    lines = []
    if topic_code and topic_name:
        lines.append(f"Đề mục: {topic_code} — {topic_name}")

    if chapter and chapter.get("number") and chapter.get("title"):
        lines.append(f"Chương {chapter['number']} — {chapter['title']}")

    if section and section.get("number") and section.get("title"):
        lines.append(f"Mục {section['number']} — {section['title']}")

    if article_code and article_title:
        lines.append(f"Điều {article_code} — {article_title}")

    if clause_number is not None:
        lines.append(f"Khoản: {clause_number}")

    if point_labels:
        lines.append(f"Điểm: {', '.join(point_labels)}")

    if source_type:
        lines.append(f"Nguồn: {source_type}")

    lines.append("")  # blank line
    lines.append(body_text)

    return "\n".join(lines).strip()


def _build_relation_lists(
    relations: list[dict],
) -> tuple[list[str | None], list[str], list[str], list[bool], list[bool]]:
    """Deduplicate relations giữ đúng thứ tự."""
    seen = set()
    target_ids = []
    target_codes = []
    types = []
    same_topics = []
    in_corpus = []

    for r in relations:
        target_id = r.get("target_id")
        target_code = r.get("target_code")
        rtype = r.get("relation_type")
        href = r.get("href")

        rkey = (target_id, target_code, rtype, href)
        if rkey in seen:
            continue
        seen.add(rkey)

        target_ids.append(target_id)
        target_codes.append(target_code)
        types.append(rtype)
        same_topics.append(r.get("same_topic", False))
        in_corpus.append(r.get("target_in_corpus", False))

    return target_ids, target_codes, types, same_topics, in_corpus


def _build_attachment_metadata(attachments: list[dict]) -> list[dict]:
    """Copy attachments thành list mới JSON-safe, không mutate original."""
    res = []
    for a in attachments:
        res.append({
            "attachment_id": a.get("attachment_id"),
            "filename": a.get("filename"),
            "href": a.get("href"),
            "file_extension": a.get("file_extension"),
            "downloaded": a.get("downloaded", False)
        })
    return res


def _create_chunk(
    article: dict,
    *,
    chunk_type: str,
    unit_type: str,
    clause_number: str | None = None,
    clause_occurrence: int | None = None,
    point_labels: list[str] | None = None,
    point_occurrences: list[int] | None = None,
    segment_index: int | None = None,
    table_id: str | None = None,
    table_index: int | None = None,
    body_text: str,
    source_unit_ids: list[str],
    context_unit_ids: list[str] | None = None,
    token_counter: TokenCounter,
    requires_fallback: bool = False,
    oversized_reason: str | None = None,
    warnings: list[str] | None = None,
    fallback_source_reason: str | None = None,
) -> dict:
    """Helper chung dựng chunk metadata và content."""
    chunk_key = build_chunk_key(
        article_id=article["article_id"],
        chunk_type=chunk_type,
        clause_number=clause_number,
        clause_occurrence=clause_occurrence,
        point_labels=point_labels,
        point_occurrences=point_occurrences,
        table_index=table_index,
        segment_index=segment_index
    )
    chunk_id = make_chunk_id(chunk_key)

    content = _build_chunk_content(
        article,
        body_text=body_text,
        clause_number=clause_number,
        point_labels=point_labels
    )
    token_count = token_counter.count(content)

    chapter = article.get("chapter") or {}
    section = article.get("section") or {}

    rel_ids, rel_codes, rel_types, rel_same, rel_in_corpus = _build_relation_lists(article.get("relations", []))
    attachment_meta = _build_attachment_metadata(article.get("attachments", []))

    return {
        "chunk_id": chunk_id,
        "chunk_key": chunk_key,
        "parent_article_id": article["article_id"],
        "document_id": article.get("document_id"),
        "topic_code": article.get("topic_code"),
        "topic_name": article.get("topic_name"),
        "article_code": article.get("article_code"),
        "codification_code": article.get("codification_code"),
        "article_title": article.get("article_title"),
        "heading": article.get("heading"),
        "chapter_id": chapter.get("anchor_id"),
        "chapter_number": chapter.get("number"),
        "chapter_title": chapter.get("title"),
        "section_id": section.get("anchor_id") if section else None,
        "section_number": section.get("number") if section else None,
        "section_title": section.get("title") if section else None,
        "chunk_type": chunk_type,
        "unit_type": unit_type,
        "clause_number": clause_number,
        "clause_occurrence": clause_occurrence,
        "point_labels": point_labels or [],
        "point_occurrences": point_occurrences or [],
        "source_unit_ids": source_unit_ids,
        "context_unit_ids": context_unit_ids or [],
        "table_id": table_id,
        "table_index": table_index,
        "segment_index": segment_index,
        "content": content,
        "body_text": body_text,
        "token_count": token_count,
        "tokenizer_name": token_counter.name,
        "source_type": article.get("source_type"),
        "source_document_id": article.get("source_document_id"),
        "source_note_text": article.get("source_note_text"),
        "source_urls": list(article.get("source_urls", [])),
        "relation_target_ids": rel_ids,
        "relation_target_codes": rel_codes,
        "relation_types": rel_types,
        "relation_same_topic": rel_same,
        "relation_target_in_corpus": rel_in_corpus,
        "attachment_metadata": attachment_meta,
        "parser_version": article.get("parser_version"),
        "chunker_version": CHUNKER_VERSION,
        "source_sha256": article.get("source_sha256"),
        "warnings": warnings or [],
        "requires_fallback": requires_fallback,
        "oversized_reason": oversized_reason,
        "fallback_source_reason": fallback_source_reason,
    }


def split_oversized_legal_text(
    text: str,
    *,
    config: ChunkingConfig,
    token_counter: TokenCounter,
) -> list[str]:
    """Split oversized legal text using RecursiveCharacterTextSplitter.

    Ensures correct order preservation, Vietnamese-friendly separators,
    and returns non-empty stripped segments.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not text.strip():
        return []

    separators = [
        "\n\n",
        "\n",
        ". ",
        "? ",
        "! ",
        "; ",
        ": ",
        ", ",
        " ",
        ""
    ]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.target_tokens,
        chunk_overlap=config.fallback_overlap,
        length_function=token_counter.count,
        separators=separators,
        keep_separator=True
    )
    raw_segments = splitter.split_text(text)
    segments = []
    for seg in raw_segments:
        s = seg.strip()
        if s:
            segments.append(s)
    return segments


def _serialize_table(
    table: dict,
    *,
    rows: list | None = None,
    include_header: bool = True,
) -> str:
    """Serialize table dictionary to deterministic, clean markdown-like format."""
    table_id = table.get("table_id", "")
    lines = [f"Bảng: {table_id}"]

    headers = table.get("headers") or []
    table_rows = rows if rows is not None else table.get("rows")

    if table_rows:
        if include_header and headers:
            header_str = " | ".join(str(h).strip() for h in headers)
            lines.append(f"Cột: {header_str}")

        for r_idx, row in enumerate(table_rows, 1):
            if isinstance(row, list):
                row_str = " | ".join(str(val) if val is not None else "" for val in row)
            elif isinstance(row, dict):
                if headers:
                    row_str = " | ".join(str(row.get(h)) if row.get(h) is not None else "" for h in headers)
                else:
                    row_str = " | ".join(str(value) if value is not None else "" for value in row.values())
            else:
                row_str = str(row)
            lines.append(f"Dòng {r_idx}: {row_str}")
        return "\n".join(lines)
    else:
        text_content = table.get("text", "").strip()
        if text_content:
            lines.append(text_content)
            return "\n".join(lines)
        return f"Bảng: {table_id}"


def _build_table_chunks(
    article: dict,
    cfg: ChunkingConfig,
    tc: TokenCounter,
) -> list[dict]:
    """Tạo table chunks cho các tables trong article['tables']."""
    tables = article.get("tables") or []
    table_chunks = []

    for tbl_idx, table in enumerate(tables, 1):
        table_id = table.get("table_id")
        rows = table.get("rows") or []

        # 1. Thử xem toàn bộ bảng có vừa khít max_tokens không
        full_body = _serialize_table(table, include_header=True)
        # Tính toán content đầy đủ bao gồm breadcrumbs
        full_content = _build_chunk_content(
            article,
            body_text=full_body
        )
        if tc.count(full_content) <= cfg.max_tokens:
            # Vừa khít, tạo 1 chunk duy nhất
            chunk = _create_chunk(
                article,
                chunk_type="table",
                unit_type="table",
                segment_index=1,
                table_id=table_id,
                table_index=tbl_idx,
                body_text=full_body,
                source_unit_ids=[],
                token_counter=tc,
                requires_fallback=False
            )
            table_chunks.append(chunk)
            continue

        # 2. Bảng vượt quá max_tokens -> cần chia nhỏ
        if rows:
            # Danh sách chứa các tuple: (group_rows, include_header, warnings)
            segments_rows = []
            current_group = []

            for r_idx, row in enumerate(rows, 1):
                # Thử thêm row vào current_group
                test_group = current_group + [row]
                test_body = _serialize_table(table, rows=test_group, include_header=True)
                test_content = _build_chunk_content(article, body_text=test_body)

                if tc.count(test_content) <= cfg.max_tokens:
                    current_group.append(row)
                else:
                    # Nếu nhóm hiện tại đã có hàng, chốt nhóm hiện tại
                    if current_group:
                        segments_rows.append((current_group, True, []))
                        current_group = [row]
                    else:
                        current_group = [row]

                    # Kiểm tra xem hàng đơn lẻ này (cộng header) có vượt max_tokens không
                    single_body = _serialize_table(table, rows=current_group, include_header=True)
                    single_content = _build_chunk_content(article, body_text=single_body)

                    if tc.count(single_content) > cfg.max_tokens:
                        # Row riêng quá dài! Cần split row này
                        row_text_only = _serialize_table(table, rows=current_group, include_header=False)
                        # Dùng split_oversized_legal_text để chia nhỏ row text
                        sub_texts = split_oversized_legal_text(row_text_only, config=cfg, token_counter=tc)
                        if not sub_texts:
                            raise ValueError(
                                f"Cannot split row {r_idx} for table {table_id} in article {article['article_id']}"
                            )

                        for sub_t in sub_texts:
                            # Thử lặp header
                            sub_body_with_hdr = f"Bảng: {table_id}\n"
                            if table.get("headers"):
                                sub_body_with_hdr += "Cột: " + " | ".join(str(h).strip() for h in table["headers"]) + "\n"
                            sub_body_with_hdr += f"Dòng: {sub_t}"

                            sub_content = _build_chunk_content(article, body_text=sub_body_with_hdr)
                            if tc.count(sub_content) <= cfg.max_tokens:
                                segments_rows.append(([sub_t], True, ["oversized_table_row_split"]))
                            else:
                                # Header quá dài, thử không lặp header
                                sub_body_no_hdr = f"Bảng: {table_id}\n{sub_t}"
                                sub_content_no_hdr = _build_chunk_content(article, body_text=sub_body_no_hdr)
                                if tc.count(sub_content_no_hdr) <= cfg.max_tokens:
                                    segments_rows.append(([sub_t], False, ["oversized_table_row_split"]))
                                else:
                                    raise ValueError(
                                        f"Cannot fit table row chunk under max_tokens for article {article['article_id']}, "
                                        f"table {table_id}, row {r_idx}."
                                    )
                        current_group = []

            # Xử lý phần còn lại trong current_group
            if current_group:
                single_body = _serialize_table(table, rows=current_group, include_header=True)
                single_content = _build_chunk_content(article, body_text=single_body)
                if tc.count(single_content) > cfg.max_tokens:
                    row_text_only = _serialize_table(table, rows=current_group, include_header=False)
                    sub_texts = split_oversized_legal_text(row_text_only, config=cfg, token_counter=tc)
                    for sub_t in sub_texts:
                        sub_body_with_hdr = f"Bảng: {table_id}\n"
                        if table.get("headers"):
                            sub_body_with_hdr += "Cột: " + " | ".join(str(h).strip() for h in table["headers"]) + "\n"
                        sub_body_with_hdr += f"Dòng: {sub_t}"
                        sub_content = _build_chunk_content(article, body_text=sub_body_with_hdr)
                        if tc.count(sub_content) <= cfg.max_tokens:
                            segments_rows.append(([sub_t], True, ["oversized_table_row_split"]))
                        else:
                            sub_body_no_hdr = f"Bảng: {table_id}\n{sub_t}"
                            sub_content_no_hdr = _build_chunk_content(article, body_text=sub_body_no_hdr)
                            if tc.count(sub_content_no_hdr) <= cfg.max_tokens:
                                segments_rows.append(([sub_t], False, ["oversized_table_row_split"]))
                            else:
                                raise ValueError(
                                    f"Cannot fit table row chunk under max_tokens for article {article['article_id']}, "
                                    f"table {table_id}."
                                )
                else:
                    segments_rows.append((current_group, True, []))

            # Dựng các chunks từ segments_rows
            for s_idx, (group, incl_hdr, warnings) in enumerate(segments_rows, 1):
                if incl_hdr:
                    if len(group) == 1 and isinstance(group[0], str) and not group[0].startswith("Dòng"):
                        # Đây là sub_text từ row quá dài
                        seg_body = f"Bảng: {table_id}\n"
                        if table.get("headers"):
                            seg_body += "Cột: " + " | ".join(str(h).strip() for h in table["headers"]) + "\n"
                        seg_body += f"Dòng: {group[0]}"
                    else:
                        seg_body = _serialize_table(table, rows=group, include_header=True)
                else:
                    if len(group) == 1 and isinstance(group[0], str) and not group[0].startswith("Dòng"):
                        seg_body = f"Bảng: {table_id}\n{group[0]}"
                    else:
                        seg_body = _serialize_table(table, rows=group, include_header=False)

                chunk = _create_chunk(
                    article,
                    chunk_type="table",
                    unit_type="table",
                    segment_index=s_idx,
                    table_id=table_id,
                    table_index=tbl_idx,
                    body_text=seg_body,
                    source_unit_ids=[],
                    token_counter=tc,
                    requires_fallback=False,
                    warnings=warnings
                )
                table_chunks.append(chunk)

        else:
            # Table không có rows mà chỉ có text
            text_content = table.get("text", "")
            sub_texts = split_oversized_legal_text(text_content, config=cfg, token_counter=tc)
            if not sub_texts:
                sub_texts = [text_content]

            for s_idx, sub_t in enumerate(sub_texts, 1):
                seg_body = f"Bảng: {table_id}\n{sub_t}"
                sub_content = _build_chunk_content(article, body_text=seg_body)
                if tc.count(sub_content) > cfg.max_tokens:
                    raise ValueError(
                        f"Cannot fit text-only table chunk under max_tokens for article {article['article_id']}, table {table_id}."
                    )
                chunk = _create_chunk(
                    article,
                    chunk_type="table",
                    unit_type="table",
                    segment_index=s_idx,
                    table_id=table_id,
                    table_index=tbl_idx,
                    body_text=seg_body,
                    source_unit_ids=[],
                    token_counter=tc,
                    requires_fallback=False
                )
                table_chunks.append(chunk)

    return table_chunks


SUBPOINT_MARKER_RE = re.compile(r"(?:^|[\n;:])\s*([a-zđĐ])(\d+)\)")


def split_point_into_subpoints(text: str) -> list[dict]:
    matches = list(SUBPOINT_MARKER_RE.finditer(text))
    if not matches:
        return [{"text": text, "is_subpoint": False, "parent_prefix": "", "subpoint_label": None}]

    segments = []
    first_match = matches[0]
    marker_sub = re.search(r"([a-zđĐ]\d+\))", first_match.group(0))
    first_marker_start = first_match.start() + marker_sub.start()

    parent_prefix = text[:first_marker_start]
    if parent_prefix.strip():
        segments.append({
            "text": parent_prefix,
            "is_subpoint": False,
            "parent_prefix": "",
            "subpoint_label": None
        })

    for i, match in enumerate(matches):
        marker_sub = re.search(r"([a-zđĐ]\d+\))", match.group(0))
        marker_start = match.start() + marker_sub.start()

        if i + 1 < len(matches):
            next_match = matches[i + 1]
            next_marker_sub = re.search(r"([a-zđĐ]\d+\))", next_match.group(0))
            next_marker_start = next_match.start() + next_marker_sub.start()
            subpoint_text = text[marker_start:next_marker_start]
        else:
            subpoint_text = text[marker_start:]

        segments.append({
            "text": subpoint_text,
            "is_subpoint": True,
            "parent_prefix": parent_prefix,
            "subpoint_label": match.group(1).lower()
        })

    return segments


def split_main_text_to_fit(
    main_text: str,
    context_lines: list[str],
    parent_art: dict,
    clause_number: str | None,
    point_labels: list[str],
    cfg: ChunkingConfig,
    tc: TokenCounter
) -> list[str]:
    """Split primary text without overlap while preserving word boundaries.

    The previous RecursiveCharacterTextSplitter configuration could fall back
    to character-level splitting when only a very small token budget remained
    after breadcrumbs. That produced fragments such as ``Đ i ể m`` when the
    segments were reconstructed. This implementation evaluates the complete
    rendered chunk and greedily packs whole non-whitespace tokens instead.
    """
    if not isinstance(main_text, str):
        raise TypeError("main_text must be a string")
    if not main_text.strip():
        return []

    def render_body(primary_text: str) -> str:
        if not context_lines:
            return primary_text
        context_part = "[Ngữ cảnh]\n" + "\n".join(context_lines)
        body_part = "[Nội dung]\n" + primary_text
        return f"{context_part}\n\n{body_part}"

    def fits(primary_text: str) -> bool:
        content = _build_chunk_content(
            parent_art,
            body_text=render_body(primary_text),
            clause_number=clause_number,
            point_labels=point_labels,
        )
        return tc.count(content) <= cfg.max_tokens

    # Keep each word together with its following whitespace. Stripping only at
    # chunk boundaries makes reconstruction lossless after whitespace
    # normalization, without duplicating source text.
    pieces = re.findall(r"\S+(?:\s+|$)", main_text)
    if not pieces:
        return []

    parts: list[str] = []
    current = ""

    for piece in pieces:
        candidate = current + piece
        candidate_text = candidate.strip()

        if candidate_text and fits(candidate_text):
            current = candidate
            continue

        if current.strip():
            parts.append(current.strip())
            current = ""

        piece_text = piece.strip()
        if not piece_text:
            continue

        if fits(piece_text):
            current = piece
            continue

        # Extremely long no-whitespace token. Split only as a last resort,
        # choosing the largest fitting prefix deterministically.
        remaining = piece_text
        while remaining:
            low = 1
            high = len(remaining)
            best = 0

            while low <= high:
                mid = (low + high) // 2
                prefix = remaining[:mid]
                if fits(prefix):
                    best = mid
                    low = mid + 1
                else:
                    high = mid - 1

            if best == 0:
                # The breadcrumbs/context alone already consume the entire
                # synthetic budget. Splitting by character would corrupt
                # Vietnamese words (for example: "Đ i ể m"). Preserve the
                # indivisible token and let validation report the unavoidable
                # oversize condition instead of damaging source text.
                parts.append(remaining)
                remaining = ""
                break

            parts.append(remaining[:best])
            remaining = remaining[best:]

    if current.strip():
        parts.append(current.strip())

    return parts

def clean_split_punctuation(parts: list[str]) -> list[str]:
    cleaned = []
    for part in parts:
        p = part.strip()
        if p:
            cleaned.append(p)

    for i in range(1, len(cleaned)):
        lead_match = re.match(r"^([\s:;.,]+)", cleaned[i])
        if lead_match:
            punct = lead_match.group(1)
            cleaned[i] = cleaned[i][len(punct):].strip()
            cleaned[i-1] = cleaned[i-1] + punct.rstrip()

    return [p.strip() for p in cleaned if p.strip()]


def build_legal_chunks(
    canonical_corpus: dict,
    *,
    config: ChunkingConfig | None = None,
    token_counter: TokenCounter | None = None,
) -> list[dict]:
    """Triển khai structural chunking core theo Điều → Khoản → nhóm Điểm."""
    if not isinstance(canonical_corpus, dict):
        raise TypeError("canonical_corpus must be a dict")

    if "metadata" not in canonical_corpus:
        raise ValueError("canonical_corpus must contain 'metadata'")
    if "articles" not in canonical_corpus:
        raise ValueError("canonical_corpus must contain 'articles'")

    if not isinstance(canonical_corpus["metadata"], dict):
        raise TypeError("metadata must be a dict")
    if not isinstance(canonical_corpus["articles"], list):
        raise TypeError("articles must be a list")

    cfg = config if config is not None else ChunkingConfig()
    tc = token_counter if token_counter is not None else get_default_token_counter()

    initial_chunks = []

    # Phase 1: Structural boundaries chunking
    for article in canonical_corpus["articles"]:
        if not isinstance(article, dict):
            raise TypeError("article must be a dict")
        if "article_id" not in article:
            raise ValueError("article must contain 'article_id'")
        if "content_units" not in article:
            raise ValueError("article must contain 'content_units'")

        art_id = article["article_id"]

        # 1. Trích xuất text units (bỏ table)
        text_units = [u for u in article.get("content_units", []) if u.get("unit_type") != "table"]
        if not text_units:
            # Article không có text units nhưng có thể có tables
            table_chunks = _build_table_chunks(article, cfg, tc)
            initial_chunks.extend(table_chunks)
            logger.warning(f"Article {art_id} has no text units")
            continue

        # 2. Xây candidate body text toàn bộ article
        art_body = _build_body_text(text_units)
        art_source_ids = [u["unit_id"] for u in text_units]

        art_candidate_content = _build_chunk_content(
            article,
            body_text=art_body
        )
        art_tokens = tc.count(art_candidate_content)

        # 3. Article Short Chunk <= max_tokens
        if art_tokens <= cfg.max_tokens:
            chunk = _create_chunk(
                article,
                chunk_type="article",
                unit_type="article",
                body_text=art_body,
                source_unit_ids=art_source_ids,
                token_counter=tc
            )

            # Verification: coverage check
            _verify_coverage(art_id, text_units, [chunk])

            table_chunks = _build_table_chunks(article, cfg, tc)
            initial_chunks.append(chunk)
            initial_chunks.extend(table_chunks)
            continue

        # 4. Article Long Chunk > max_tokens
        preamble_units, clause_groups, orphan_units, group_warnings = _group_text_units(article)

        article_chunks = []

        # Xử lý Preamble (chỉ xử lý riêng nếu có clause groups)
        preamble_attached = False
        if preamble_units and clause_groups:
            preamble_body = _build_body_text(preamble_units)
            # Thử gộp preamble với clause group đầu tiên
            # deep copy để không mutate clause_groups
            cg0 = clause_groups[0]
            cg0_units = cg0["ordered_units"]
            combined_body = preamble_body + "\n" + _build_body_text(cg0_units)
            combined_content = _build_chunk_content(
                article,
                body_text=combined_body,
                clause_number=cg0["clause_number"]
            )
            if tc.count(combined_content) <= cfg.max_tokens:
                # Gộp preamble vào clause_group 0
                cg0["ordered_units"] = preamble_units + cg0_units
                cg0["source_unit_ids"] = [u["unit_id"] for u in preamble_units] + cg0["source_unit_ids"]
                preamble_attached = True

            if not preamble_attached:
                # Tạo chunk preamble riêng
                preamble_content = _build_chunk_content(article, body_text=preamble_body)
                p_tokens = tc.count(preamble_content)
                req_fb = p_tokens > cfg.max_tokens
                ovr_r = "oversized_preamble" if req_fb else None

                p_chunk = _create_chunk(
                    article,
                    chunk_type="preamble",
                    unit_type="preamble",
                    body_text=preamble_body,
                    source_unit_ids=[u["unit_id"] for u in preamble_units],
                    token_counter=tc,
                    requires_fallback=req_fb,
                    oversized_reason=ovr_r,
                    warnings=group_warnings
                )
                article_chunks.append(p_chunk)

        # Xử lý các Clause Groups
        if clause_groups:
            for cg in clause_groups:
                cg_body = _build_body_text(cg["ordered_units"])
                cg_content = _build_chunk_content(
                    article,
                    body_text=cg_body,
                    clause_number=cg["clause_number"],
                    point_labels=cg["point_labels"]
                )
                cg_tokens = tc.count(cg_content)

                if cg_tokens <= cfg.max_tokens:
                    # Clause <= max_tokens
                    c_chunk = _create_chunk(
                        article,
                        chunk_type="clause",
                        unit_type="clause",
                        clause_number=cg["clause_number"],
                        clause_occurrence=cg["clause_occurrence"],
                        point_labels=cg["point_labels"],
                        point_occurrences=cg["point_occurrences"],
                        body_text=cg_body,
                        source_unit_ids=cg["source_unit_ids"],
                        token_counter=tc,
                        warnings=cg["warnings"] + group_warnings
                    )
                    article_chunks.append(c_chunk)
                else:
                    # Clause > max_tokens
                    # Có points?
                    point_units = [u for u in cg["ordered_units"] if u.get("unit_type") == "point"]
                    if point_units:
                        # Chia point groups greedy
                        clause_lead = []
                        remaining_units = []
                        found_first_point = False
                        for u in cg["ordered_units"]:
                            if u.get("unit_type") == "point":
                                found_first_point = True
                            if not found_first_point:
                                clause_lead.append(u)
                            else:
                                remaining_units.append(u)

                        # Nếu clause_lead vượt max_tokens, tạo chunk fallback riêng cho clause lead
                        if clause_lead:
                            lead_body = _build_body_text(clause_lead)
                            lead_content = _build_chunk_content(
                                article,
                                body_text=lead_body,
                                clause_number=cg["clause_number"]
                            )
                            if tc.count(lead_content) > cfg.max_tokens:
                                lead_chunk = _create_chunk(
                                    article,
                                    chunk_type="fallback_segment",
                                    unit_type="clause",
                                    clause_number=cg["clause_number"],
                                    clause_occurrence=cg["clause_occurrence"],
                                    segment_index=1,
                                    body_text=lead_body,
                                    source_unit_ids=[u["unit_id"] for u in clause_lead],
                                    token_counter=tc,
                                    requires_fallback=True,
                                    oversized_reason="oversized_clause_lead",
                                    warnings=cg["warnings"] + group_warnings
                                )
                                article_chunks.append(lead_chunk)
                                # clause_lead will NOT be appended as context prefix to points
                                clause_context_lead = []
                            else:
                                clause_context_lead = clause_lead
                        else:
                            clause_context_lead = []

                        # Gom point unit và các continuation liền sau nó thành một block để bảo đảm
                        # mỗi group luôn chứa ít nhất 1 point unit, giải quyết duplicate keys
                        point_blocks = []
                        current_block = None
                        for u in remaining_units:
                            if u.get("unit_type") == "point":
                                if current_block:
                                    point_blocks.append(current_block)
                                current_block = [u]
                            else:
                                if current_block:
                                    current_block.append(u)
                                else:
                                    clause_lead.append(u)
                        if current_block:
                            point_blocks.append(current_block)

                        # Gom nhóm greedy các point_blocks
                        current_group_blocks = []

                        def make_points_chunk(blocks_to_chunk):
                            flat_units = []
                            for b in blocks_to_chunk:
                                flat_units.extend(b)

                            sub_labels = [u.get("point_label") for u in flat_units if u.get("unit_type") == "point"]
                            sub_occs = [u.get("unit_occurrence", 1) for u in flat_units if u.get("unit_type") == "point"]

                            sub_body = _build_body_text(clause_context_lead + flat_units)
                            sub_source_ids = [u["unit_id"] for u in clause_context_lead + flat_units]

                            sub_content = _build_chunk_content(
                                article,
                                body_text=sub_body,
                                clause_number=cg["clause_number"],
                                point_labels=sub_labels
                            )
                            sub_tokens = tc.count(sub_content)

                            req_fb = sub_tokens > cfg.max_tokens
                            ovr_r = "oversized_point" if req_fb else None

                            return _create_chunk(
                                article,
                                chunk_type="points",
                                unit_type="point_group",
                                clause_number=cg["clause_number"],
                                clause_occurrence=cg["clause_occurrence"],
                                point_labels=sub_labels,
                                point_occurrences=sub_occs,
                                body_text=sub_body,
                                source_unit_ids=sub_source_ids,
                                token_counter=tc,
                                requires_fallback=req_fb,
                                oversized_reason=ovr_r,
                                warnings=cg["warnings"] + group_warnings
                            )

                        idx = 0
                        while idx < len(point_blocks):
                            block_candidate = point_blocks[idx]

                            if not current_group_blocks:
                                current_group_blocks.append(block_candidate)
                                idx += 1
                                continue

                            # Thử thêm block_candidate vào nhóm hiện tại
                            test_blocks = current_group_blocks + [block_candidate]
                            flat_test_units = []
                            for b in test_blocks:
                                flat_test_units.extend(b)

                            test_labels = [u.get("point_label") for u in flat_test_units if u.get("unit_type") == "point"]
                            test_body = _build_body_text(clause_context_lead + flat_test_units)
                            test_content = _build_chunk_content(
                                article,
                                body_text=test_body,
                                clause_number=cg["clause_number"],
                                point_labels=test_labels
                            )
                            test_tokens = tc.count(test_content)

                            if test_tokens <= cfg.max_tokens:
                                current_group_blocks.append(block_candidate)
                                idx += 1
                            else:
                                article_chunks.append(make_points_chunk(current_group_blocks))
                                current_group_blocks = []

                        if current_group_blocks:
                            article_chunks.append(make_points_chunk(current_group_blocks))

                    else:
                        # Clause > max_tokens và KHÔNG CÓ points
                        c_chunk = _create_chunk(
                            article,
                            chunk_type="fallback_segment",
                            unit_type="clause",
                            clause_number=cg["clause_number"],
                            clause_occurrence=cg["clause_occurrence"],
                            segment_index=1,
                            body_text=cg_body,
                            source_unit_ids=cg["source_unit_ids"],
                            token_counter=tc,
                            requires_fallback=True,
                            oversized_reason="oversized_clause_without_points",
                            warnings=cg["warnings"] + group_warnings
                        )
                        article_chunks.append(c_chunk)

        else:
            # Article không có clause: preamble vẫn là nội dung cấp Điều,
            # còn orphan/unsupported units được xử lý riêng ở nhánh bên dưới.
            if preamble_units:
                no_clause_body = _build_body_text(preamble_units)
                art_chunk = _create_chunk(
                    article,
                    chunk_type="fallback_segment",
                    unit_type="article",
                    segment_index=1,
                    body_text=no_clause_body,
                    source_unit_ids=[u["unit_id"] for u in preamble_units],
                    token_counter=tc,
                    requires_fallback=True,
                    oversized_reason="oversized_article_without_clause",
                    warnings=group_warnings
                )
                article_chunks.append(art_chunk)

        # Xử lý Orphan Units
        if orphan_units:
            orphan_body = _build_body_text(orphan_units)
            orphan_content = _build_chunk_content(article, body_text=orphan_body)
            o_tokens = tc.count(orphan_content)
            req_fb = o_tokens > cfg.max_tokens
            ovr_r = "oversized_orphan_units" if req_fb else None

            o_chunk = _create_chunk(
                article,
                chunk_type="fallback_segment",
                unit_type="orphan",
                body_text=orphan_body,
                source_unit_ids=[u["unit_id"] for u in orphan_units],
                token_counter=tc,
                requires_fallback=req_fb,
                oversized_reason=ovr_r,
                warnings=group_warnings
            )
            article_chunks.append(o_chunk)

        # Verification: coverage check
        _verify_coverage(art_id, text_units, article_chunks)
        table_chunks = _build_table_chunks(article, cfg, tc)
        initial_chunks.extend(article_chunks)
        initial_chunks.extend(table_chunks)

    # Phase 2: Fallback splitting for chunks that require fallback
    final_chunks = []
    for chunk in initial_chunks:
        if not chunk.get("requires_fallback"):
            chunk["fallback_source_reason"] = None
            if "context_unit_ids" not in chunk:
                chunk["context_unit_ids"] = []
            final_chunks.append(chunk)
            continue

        # Extract information from oversized chunk
        orig_reason = chunk.get("oversized_reason")
        article_id = chunk.get("parent_article_id")

        parent_art = next((art for art in canonical_corpus["articles"] if art["article_id"] == article_id), None)
        if not parent_art:
            raise ValueError(f"Parent article {article_id} not found in corpus")

        # Map unit_id to canonical unit dict
        unit_map = {u["unit_id"]: u for u in parent_art.get("content_units", [])}

        # Retrieve the ordered list of canonical units for this chunk
        chunk_units = [unit_map[uid] for uid in chunk.get("source_unit_ids", []) if uid in unit_map]

        # Identify clause intro context if present
        clause_intro_text = ""
        clause_intro_id = None
        if chunk.get("clause_number"):
            for u in parent_art.get("content_units", []):
                if u.get("unit_type") == "clause" and u.get("clause_number") == chunk.get("clause_number"):
                    clause_intro_text = u.get("text", "").strip()
                    clause_intro_id = u["unit_id"]
                    break

        # A clause unit inside point_group is repeated context, not primary
        # content. Emitting it again creates a duplicate clause segment.
        if chunk.get("unit_type") == "point_group":
            primary_units = [
                unit
                for unit in chunk_units
                if unit.get("unit_type") != "clause"
            ]
        else:
            primary_units = chunk_units

        # Build subpoint segments from primary units
        primary_subpoints = []
        for u in primary_units:
            if u.get("unit_type") == "point":
                parts = split_point_into_subpoints(u.get("text", ""))
                for part in parts:
                    primary_subpoints.append({
                        "text": part["text"],
                        "source_unit_id": u["unit_id"],
                        "unit_type": u["unit_type"],
                        "point_label": part["subpoint_label"] if part["is_subpoint"] else u.get("point_label"),
                        "parent_prefix": part["parent_prefix"],
                        "is_subpoint": part["is_subpoint"]
                    })
            else:
                primary_subpoints.append({
                    "text": u.get("text", ""),
                    "source_unit_id": u["unit_id"],
                    "unit_type": u["unit_type"],
                    "point_label": u.get("point_label"),
                    "parent_prefix": "",
                    "is_subpoint": False
                })

        # Flat split any oversized subpoint segment
        flat_subpoints = []
        for sp in primary_subpoints:
            context_lines = []
            if clause_intro_text and sp["unit_type"] != "clause":
                context_lines.append(clause_intro_text)
            if sp["parent_prefix"]:
                context_lines.append(sp["parent_prefix"])

            test_body = sp["text"]
            if context_lines:
                test_body = "[Ngữ cảnh]\n" + "\n".join(context_lines) + "\n\n[Nội dung]\n" + sp["text"]

            test_content = _build_chunk_content(
                parent_art,
                body_text=test_body,
                clause_number=chunk.get("clause_number"),
                point_labels=[sp["point_label"]] if sp["point_label"] else []
            )

            if tc.count(test_content) <= cfg.max_tokens:
                flat_parts = [sp["text"]]
            else:
                flat_parts = split_main_text_to_fit(
                    sp["text"],
                    context_lines,
                    parent_art,
                    chunk.get("clause_number"),
                    [sp["point_label"]] if sp["point_label"] else [],
                    cfg,
                    tc
                )
                flat_parts = clean_split_punctuation(flat_parts)

            for part in flat_parts:
                flat_subpoints.append({
                    "text": part,
                    "source_unit_id": sp["source_unit_id"],
                    "unit_type": sp["unit_type"],
                    "point_label": sp["point_label"],
                    "parent_prefix": sp["parent_prefix"],
                    "is_subpoint": sp["is_subpoint"]
                })

        # Greedy packing of flat_subpoints into segments
        repaired_segments_groups = []
        current_group = []
        for f_sp in flat_subpoints:
            # Force split on transition between clause and other unit types
            if current_group and (f_sp["unit_type"] == "clause" or current_group[-1]["unit_type"] == "clause"):
                repaired_segments_groups.append(current_group)
                current_group = [f_sp]
                continue

            candidate_group = current_group + [f_sp]

            candidate_body_texts = [item["text"] for item in candidate_group]
            candidate_main_text = "\n".join(candidate_body_texts)

            candidate_context_lines = []
            if clause_intro_text and any(item["unit_type"] != "clause" for item in candidate_group):
                candidate_context_lines.append(clause_intro_text)
            seen_prefixes = set()
            for item in candidate_group:
                if item["parent_prefix"] and item["parent_prefix"] not in seen_prefixes:
                    seen_prefixes.add(item["parent_prefix"])
                    candidate_context_lines.append(item["parent_prefix"])

            if candidate_context_lines:
                context_part = "[Ngữ cảnh]\n" + "\n".join(candidate_context_lines)
                body_part = "[Nội dung]\n" + candidate_main_text
                candidate_segment_text = f"{context_part}\n\n{body_part}"
            else:
                candidate_segment_text = candidate_main_text

            candidate_labels = []
            seen_lbls = set()
            for item in candidate_group:
                lbl = item["point_label"]
                if lbl and lbl not in seen_lbls:
                    seen_lbls.add(lbl)
                    candidate_labels.append(lbl)

            candidate_content = _build_chunk_content(
                parent_art,
                body_text=candidate_segment_text,
                clause_number=chunk.get("clause_number"),
                point_labels=candidate_labels
            )

            if tc.count(candidate_content) <= cfg.max_tokens or not current_group:
                current_group = candidate_group
            else:
                repaired_segments_groups.append(current_group)
                current_group = [f_sp]

        if current_group:
            repaired_segments_groups.append(current_group)

        # Re-create fallback segments chunks
        for s_idx, group in enumerate(repaired_segments_groups, 1):
            body_texts = [item["text"] for item in group]
            main_text = "\n".join(body_texts)

            context_lines = []
            if clause_intro_text and any(item["unit_type"] != "clause" for item in group):
                context_lines.append(clause_intro_text)
            seen_prefixes = set()
            for item in group:
                if item["parent_prefix"] and item["parent_prefix"] not in seen_prefixes:
                    seen_prefixes.add(item["parent_prefix"])
                    context_lines.append(item["parent_prefix"])

            if context_lines:
                context_part = "[Ngữ cảnh]\n" + "\n".join(context_lines)
                body_part = "[Nội dung]\n" + main_text
                segment_body = f"{context_part}\n\n{body_part}"
            else:
                segment_body = main_text

            point_labels = []
            seen_lbls = set()
            for item in group:
                lbl = item["point_label"]
                if lbl and lbl not in seen_lbls:
                    seen_lbls.add(lbl)
                    point_labels.append(lbl)

            source_unit_ids = []
            seen_sids = set()
            for item in group:
                uid = item["source_unit_id"]
                if uid and uid not in seen_sids:
                    seen_sids.add(uid)
                    source_unit_ids.append(uid)

            context_unit_ids = []
            if clause_intro_id and any(item["unit_type"] != "clause" for item in group):
                context_unit_ids.append(clause_intro_id)
            for item in group:
                if item["parent_prefix"]:
                    uid = item["source_unit_id"]
                    if uid and uid not in context_unit_ids:
                        context_unit_ids.append(uid)

            orig_labels = chunk.get("point_labels") or []
            orig_occs = chunk.get("point_occurrences") or []
            label_to_occ = dict(zip(orig_labels, orig_occs))
            point_occurrences = [label_to_occ.get(lbl, 1) for lbl in point_labels]

            seg_chunk = _create_chunk(
                parent_art,
                chunk_type="fallback_segment",
                unit_type=chunk.get("unit_type"),
                clause_number=chunk.get("clause_number"),
                clause_occurrence=chunk.get("clause_occurrence"),
                point_labels=point_labels,
                point_occurrences=point_occurrences,
                segment_index=s_idx,
                body_text=segment_body,
                source_unit_ids=source_unit_ids,
                context_unit_ids=context_unit_ids,
                token_counter=tc,
                requires_fallback=False,
                warnings=chunk.get("warnings", []),
                fallback_source_reason=orig_reason
            )
            final_chunks.append(seg_chunk)

    # Double check total corpus coverage
    all_input_unit_ids = set()
    for art in canonical_corpus["articles"]:
        for u in art.get("content_units", []):
            if u.get("unit_type") != "table":
                all_input_unit_ids.add(u["unit_id"])

    all_output_unit_ids = set()
    for chunk in final_chunks:
        all_output_unit_ids.update(chunk.get("source_unit_ids") or [])
        # Context units are exact canonical text repeated for legal coherence.
        # They count as preserved coverage but remain separate from primary
        # provenance in source_unit_ids.
        all_output_unit_ids.update(chunk.get("context_unit_ids") or [])

    missing_ids = all_input_unit_ids - all_output_unit_ids
    if missing_ids:
        raise ValueError(
            f"Data loss detected in final output: non-table unit_ids {missing_ids} are not covered by any chunk."
        )

    # Final invariant: duplicate keys/IDs are generation bugs. Do not hide
    # them by silently dropping one of the chunks.
    seen_chunk_keys: set[str] = set()
    seen_chunk_ids: set[str] = set()
    for candidate in final_chunks:
        candidate_key = candidate["chunk_key"]
        candidate_id = candidate["chunk_id"]
        if candidate_key in seen_chunk_keys:
            raise ValueError(f"duplicate final chunk_key: {candidate_key}")
        if candidate_id in seen_chunk_ids:
            raise ValueError(f"duplicate final chunk_id: {candidate_id}")
        seen_chunk_keys.add(candidate_key)
        seen_chunk_ids.add(candidate_id)

    return final_chunks


def _verify_coverage(article_id: str, original_text_units: list[dict], generated_chunks: list[dict]) -> None:
    """Kiểm tra xem tất cả các non-table text unit_id của article có nằm trong ít nhất một chunk hay không."""
    covered_ids = set()
    for chunk in generated_chunks:
        covered_ids.update(chunk["source_unit_ids"])

    for u in original_text_units:
        uid = u["unit_id"]
        if uid not in covered_ids:
            raise ValueError(
                f"Data loss detected in article {article_id}: unit_id {uid} is not covered by any chunk."
            )


def validate_chunks(
    canonical_corpus: dict,
    chunks: list[dict],
    *,
    config: ChunkingConfig | None = None,
    token_counter: TokenCounter | None = None,
) -> dict:
    """
    Validate output chunks against canonical corpus structural rules and chunk schemas.
    """
    # 1. Validate inputs
    if not isinstance(canonical_corpus, dict):
        raise TypeError("canonical_corpus must be a dict")
    if "articles" not in canonical_corpus:
        raise ValueError("canonical_corpus must contain 'articles'")
    if not isinstance(canonical_corpus["articles"], list):
        raise TypeError("canonical_corpus['articles'] must be a list")
    for idx, art in enumerate(canonical_corpus["articles"]):
        if not isinstance(art, dict):
            raise TypeError(f"Article at index {idx} must be a dict")
        if "article_id" not in art:
            raise ValueError(f"Article at index {idx} must contain 'article_id'")

    if not isinstance(chunks, list):
        raise TypeError("chunks must be a list")
    for idx, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            raise TypeError(f"Chunk at index {idx} must be a dict")

    # Save signatures to detect mutation
    import json
    import copy
    orig_corpus_json = json.dumps(canonical_corpus, sort_keys=True, ensure_ascii=False)
    orig_chunks_json = json.dumps(chunks, sort_keys=True, ensure_ascii=False)
    orig_chunks_len = len(chunks)

    # Use defaults if None
    cfg = config if config is not None else ChunkingConfig()
    tc = token_counter if token_counter is not None else get_default_token_counter()

    errors = []
    warnings = []

    # Check if empty chunks
    if not chunks:
        errors.append("chunks_is_empty: Chunks list is empty")

    REQUIRED_KEYS = [
        "chunk_id", "chunk_key", "parent_article_id", "document_id", "topic_code", "topic_name",
        "article_code", "chunk_type", "unit_type", "content", "body_text", "token_count",
        "tokenizer_name", "source_unit_ids", "context_unit_ids", "table_id", "table_index", "segment_index",
        "relation_target_ids", "relation_target_codes", "relation_types", "relation_same_topic",
        "relation_target_in_corpus", "attachment_metadata", "parser_version", "chunker_version",
        "requires_fallback", "oversized_reason", "warnings"
    ]

    VALID_CHUNK_TYPES = {"article", "clause", "points", "preamble", "fallback_segment", "table"}
    VALID_UNIT_TYPES = {"article", "clause", "point_group", "preamble", "orphan", "table"}

    # Track metrics
    article_count = len(canonical_corpus["articles"])
    chunk_count = len(chunks)

    canonical_article_ids = {art["article_id"] for art in canonical_corpus["articles"]}
    covered_article_ids = set()

    duplicate_chunk_id_count = 0
    duplicate_chunk_ids = set()
    seen_chunk_ids = set()

    duplicate_chunk_key_count = 0
    duplicate_chunk_keys = set()
    seen_chunk_keys = set()

    empty_content_count = 0
    empty_content_chunk_ids = []

    empty_body_count = 0
    empty_body_chunk_ids = []

    invalid_parent_count = 0
    invalid_parent_chunk_ids = []
    invalid_parent_article_ids = set()

    missing_required_field_count = 0
    chunks_missing_required_fields = []

    invalid_chunk_type_count = 0
    invalid_chunk_type_chunks = []

    invalid_unit_type_count = 0
    invalid_unit_type_chunks = []

    token_count_mismatch_count = 0
    token_count_mismatch_chunks = []

    tokenizer_name_mismatch_count = 0
    tokenizer_name_mismatch_chunks = []

    oversized_chunk_count = 0
    oversized_chunks = []
    max_token_count = 0

    requires_fallback_count = 0
    fallback_pending_chunks = []

    stable_id_check = True
    unstable_id_chunks = []

    JSON_serialization_error_count = 0
    JSON_serialization_error_chunks = []

    relation_list_length_mismatch_count = 0
    relation_list_length_mismatch_chunks = []

    # Table segment identities
    duplicate_table_segment_key_count = 0
    duplicate_table_segment_keys = set()
    seen_table_segments = set()

    invalid_table_metadata_count = 0
    invalid_table_metadata_chunks = []

    table_segment_sequence_error_count = 0
    table_segment_sequence_errors = []

    table_ordering_error_count = 0
    table_ordering_error_articles = set()

    # Non-table unit IDs
    canonical_non_table_units = set()
    for art in canonical_corpus["articles"]:
        for u in art.get("content_units", []):
            if u.get("unit_type") != "table":
                canonical_non_table_units.add(u["unit_id"])

    covered_non_table_units = set()
    unknown_source_unit_ids = set()

    # Table IDs
    canonical_table_ids = set()
    for art in canonical_corpus["articles"]:
        for t in art.get("tables", []):
            if t.get("table_id"):
                canonical_table_ids.add(t["table_id"])

    covered_table_ids = set()

    # Chunks of each article to validate sequence and ordering
    article_chunks_map = {}

    import uuid

    # 2. Loop through all chunks
    for chunk in chunks:
        c_id = chunk.get("chunk_id")
        c_key = chunk.get("chunk_key")
        c_type = chunk.get("chunk_type")
        u_type = chunk.get("unit_type")
        parent_id = chunk.get("parent_article_id")
        content = chunk.get("content")
        body_text = chunk.get("body_text")

        chunk_ident = c_id if c_id else (c_key if c_key else "missing_key_and_id")

        # Required fields check
        missing_fields = [k for k in REQUIRED_KEYS if k not in chunk]
        if missing_fields:
            missing_required_field_count += 1
            chunks_missing_required_fields.append(chunk_ident)
            errors.append(f"missing_required_fields: Chunk {chunk_ident} is missing keys: {missing_fields}")

        # Parent ID validity
        if parent_id is not None:
            if parent_id not in canonical_article_ids:
                invalid_parent_count += 1
                invalid_parent_chunk_ids.append(chunk_ident)
                invalid_parent_article_ids.add(parent_id)
                errors.append(f"invalid_parent: Chunk {chunk_ident} points to invalid article {parent_id}")
            else:
                covered_article_ids.add(parent_id)
                if parent_id not in article_chunks_map:
                    article_chunks_map[parent_id] = []
                article_chunks_map[parent_id].append(chunk)
        else:
            invalid_parent_count += 1
            invalid_parent_chunk_ids.append(chunk_ident)
            errors.append(f"invalid_parent: Chunk {chunk_ident} has parent_article_id is None")

        # Duplicate ID check
        if c_id is not None:
            if c_id in seen_chunk_ids:
                duplicate_chunk_id_count += 1
                duplicate_chunk_ids.add(c_id)
                errors.append(f"duplicate_chunk_id: {c_id}")
            seen_chunk_ids.add(c_id)

        # Duplicate Key check
        if c_key is not None:
            if c_key in seen_chunk_keys:
                duplicate_chunk_key_count += 1
                duplicate_chunk_keys.add(c_key)
                errors.append(f"duplicate_chunk_key: {c_key}")
            seen_chunk_keys.add(c_key)

        # Empty content/body check
        if not isinstance(content, str) or not content.strip() or content == "None":
            empty_content_count += 1
            empty_content_chunk_ids.append(chunk_ident)
            errors.append(f"empty_content: Chunk {chunk_ident}")

        if not isinstance(body_text, str) or not body_text.strip() or body_text == "None":
            empty_body_count += 1
            empty_body_chunk_ids.append(chunk_ident)
            errors.append(f"empty_body: Chunk {chunk_ident}")

        # Chunk type / Unit type validity
        if c_type not in VALID_CHUNK_TYPES:
            invalid_chunk_type_count += 1
            invalid_chunk_type_chunks.append(chunk_ident)
            errors.append(f"invalid_chunk_type: Chunk {chunk_ident} has type '{c_type}'")

        if u_type not in VALID_UNIT_TYPES:
            invalid_unit_type_count += 1
            invalid_unit_type_chunks.append(chunk_ident)
            errors.append(f"invalid_unit_type: Chunk {chunk_ident} has unit type '{u_type}'")

        # Stable ID check
        if c_key is not None and c_id is not None:
            try:
                expected_id = make_chunk_id(c_key)
                if c_id != expected_id:
                    stable_id_check = False
                    unstable_id_chunks.append(chunk_ident)
                    errors.append(f"unstable_id: Chunk {chunk_ident} expected {expected_id}")
                else:
                    parsed_uuid = uuid.UUID(c_id)
                    if parsed_uuid.version != 5:
                        stable_id_check = False
                        unstable_id_chunks.append(chunk_ident)
                        errors.append(f"invalid_uuid_version: Chunk {chunk_ident} is version {parsed_uuid.version} instead of 5")
            except Exception as e:
                stable_id_check = False
                unstable_id_chunks.append(chunk_ident)
                errors.append(f"invalid_uuid: Chunk {chunk_ident} UUID error: {str(e)}")

        # Token Counter Check
        tok_count = chunk.get("token_count")
        tok_name = chunk.get("tokenizer_name")
        if content is not None:
            if not isinstance(tok_count, int) or isinstance(tok_count, bool):
                token_count_mismatch_count += 1
                token_count_mismatch_chunks.append(chunk_ident)
                errors.append(f"token_count_mismatch: Chunk {chunk_ident} has invalid token_count type")
            else:
                actual_toks = tc.count(content)
                if tok_count != actual_toks:
                    token_count_mismatch_count += 1
                    token_count_mismatch_chunks.append(chunk_ident)
                    errors.append(f"token_count_mismatch: Chunk {chunk_ident} expected {actual_toks}, got {tok_count}")
                if tok_count > max_token_count:
                    max_token_count = tok_count
                if tok_count > cfg.max_tokens:
                    oversized_chunk_count += 1
                    oversized_chunks.append(chunk_ident)
                    errors.append(f"oversized_chunk: Chunk {chunk_ident} token count {tok_count} > max {cfg.max_tokens}")

        if tok_name != tc.name:
            tokenizer_name_mismatch_count += 1
            tokenizer_name_mismatch_chunks.append(chunk_ident)
            errors.append(f"tokenizer_name_mismatch: Chunk {chunk_ident} expected '{tc.name}', got '{tok_name}'")

        # Fallback pending check
        if chunk.get("requires_fallback"):
            requires_fallback_count += 1
            fallback_pending_chunks.append(chunk_ident)
            errors.append(f"fallback_pending: Chunk {chunk_ident} requires fallback")

        # Relation list length check
        rel_ids = chunk.get("relation_target_ids")
        rel_codes = chunk.get("relation_target_codes")
        rel_types = chunk.get("relation_types")
        rel_same = chunk.get("relation_same_topic")
        rel_in_corpus = chunk.get("relation_target_in_corpus")

        if all(isinstance(lst, list) for lst in [rel_ids, rel_codes, rel_types, rel_same, rel_in_corpus]):
            n_lens = {len(rel_ids), len(rel_codes), len(rel_types), len(rel_same), len(rel_in_corpus)}
            if len(n_lens) > 1:
                relation_list_length_mismatch_count += 1
                relation_list_length_mismatch_chunks.append(chunk_ident)
                errors.append(f"relation_list_length_mismatch: Chunk {chunk_ident} lists have mismatching lengths")
        elif any(lst is not None for lst in [rel_ids, rel_codes, rel_types, rel_same, rel_in_corpus]):
            relation_list_length_mismatch_count += 1
            relation_list_length_mismatch_chunks.append(chunk_ident)
            errors.append(f"relation_list_length_mismatch: Some relation lists of Chunk {chunk_ident} are None while others are not")

        # JSON Serialization check
        try:
            json.dumps(chunk, ensure_ascii=False)
        except Exception as e:
            JSON_serialization_error_count += 1
            JSON_serialization_error_chunks.append(chunk_ident)
            errors.append(f"JSON_serialization_error: Chunk {chunk_ident} failed to serialize: {str(e)}")

        # Primary source and repeated-context coverage. Context remains
        # separately identifiable in chunk metadata, but its exact canonical
        # unit must still count as preserved corpus coverage.
        src_ids = chunk.get("source_unit_ids") or []
        context_ids = chunk.get("context_unit_ids") or []
        for sid in [*src_ids, *context_ids]:
            if sid in canonical_non_table_units:
                covered_non_table_units.add(sid)
            else:
                unknown_source_unit_ids.add(sid)

        # Table ID coverage and metadata check
        tbl_id = chunk.get("table_id")
        tbl_idx = chunk.get("table_index")
        seg_idx = chunk.get("segment_index")

        if c_type == "table":
            if u_type != "table":
                invalid_table_metadata_count += 1
                invalid_table_metadata_chunks.append(chunk_ident)
                errors.append(f"invalid_table_metadata: Table chunk {chunk_ident} has unit_type '{u_type}'")
            if not tbl_id or tbl_id not in canonical_table_ids:
                invalid_table_metadata_count += 1
                invalid_table_metadata_chunks.append(chunk_ident)
                errors.append(f"invalid_table_metadata: Table chunk {chunk_ident} has invalid table_id '{tbl_id}'")
            else:
                covered_table_ids.add(tbl_id)

            if not isinstance(tbl_idx, int) or tbl_idx < 1:
                invalid_table_metadata_count += 1
                invalid_table_metadata_chunks.append(chunk_ident)
                errors.append(f"invalid_table_metadata: Table chunk {chunk_ident} has invalid table_index '{tbl_idx}'")

            if not isinstance(seg_idx, int) or seg_idx < 1:
                invalid_table_metadata_count += 1
                invalid_table_metadata_chunks.append(chunk_ident)
                errors.append(f"invalid_table_metadata: Table chunk {chunk_ident} has invalid segment_index '{seg_idx}'")

            if src_ids:
                invalid_table_metadata_count += 1
                invalid_table_metadata_chunks.append(chunk_ident)
                errors.append(f"invalid_table_metadata: Table chunk {chunk_ident} has non-empty source_unit_ids: {src_ids}")

            if chunk.get("requires_fallback"):
                invalid_table_metadata_count += 1
                invalid_table_metadata_chunks.append(chunk_ident)
                errors.append(f"invalid_table_metadata: Table chunk {chunk_ident} has requires_fallback = True")

            if chunk.get("oversized_reason") is not None:
                invalid_table_metadata_count += 1
                invalid_table_metadata_chunks.append(chunk_ident)
                errors.append(f"invalid_table_metadata: Table chunk {chunk_ident} has oversized_reason '{chunk.get('oversized_reason')}'")

            # Duplicate table segment key
            if parent_id and tbl_id and tbl_idx is not None and seg_idx is not None:
                seg_key = (parent_id, tbl_id, tbl_idx, seg_idx)
                if seg_key in seen_table_segments:
                    duplicate_table_segment_key_count += 1
                    duplicate_table_segment_keys.add(f"{parent_id}|{tbl_id}|{tbl_idx}|{seg_idx}")
                    errors.append(f"duplicate_table_segment: {seg_key}")
                seen_table_segments.add(seg_key)
        else:
            if tbl_id is not None or tbl_idx is not None:
                invalid_table_metadata_count += 1
                invalid_table_metadata_chunks.append(chunk_ident)
                errors.append(f"invalid_table_metadata: Non-table chunk {chunk_ident} has table_id '{tbl_id}' or table_index '{tbl_idx}'")

    # Missing articles
    missing_article_ids = canonical_article_ids - covered_article_ids
    for maid in missing_article_ids:
        errors.append(f"missing_article: {maid}")

    # Missing non-table units
    missing_non_table_units = canonical_non_table_units - covered_non_table_units
    for muid in missing_non_table_units:
        errors.append(f"missing_non_table_unit: {muid}")

    # Unknown source units
    for uuid_val in unknown_source_unit_ids:
        errors.append(f"unknown_source_unit: {uuid_val}")

    # Table coverage
    missing_table_ids = canonical_table_ids - covered_table_ids
    for mtid in missing_table_ids:
        errors.append(f"missing_table: {mtid}")

    # Unknown table IDs in table chunks
    unknown_table_ids = covered_table_ids - canonical_table_ids
    for utid in unknown_table_ids:
        errors.append(f"unknown_table: {utid}")

    # Table sequence check
    table_groups = {}
    for (pid, tid, t_idx, s_idx) in seen_table_segments:
        gkey = (pid, tid, t_idx)
        if gkey not in table_groups:
            table_groups[gkey] = []
        table_groups[gkey].append(s_idx)

    table_segment_sequence_errors = []
    for gkey, segs in table_groups.items():
        sorted_segs = sorted(segs)
        expected = list(range(1, len(sorted_segs) + 1))
        if sorted_segs != expected:
            table_segment_sequence_error_count += 1
            err_desc = f"article={gkey[0]}|table_id={gkey[1]}|table_index={gkey[2]}|got={sorted_segs}"
            table_segment_sequence_errors.append(err_desc)
            errors.append(f"table_segment_sequence_error: {err_desc}")

    # Table ordering & article continuous check
    table_ordering_error_articles = set()
    table_ordering_error_count = 0

    seen_articles = set()
    last_article_id = None
    seen_table_for_article = set()

    for chunk in chunks:
        art_id = chunk.get("parent_article_id")
        c_type = chunk.get("chunk_type")
        if not art_id:
            continue

        # Check article continuous (no interleaving)
        if art_id != last_article_id:
            if art_id in seen_articles:
                table_ordering_error_articles.add(art_id)
                errors.append(f"article_interleaved: Article {art_id} is interleaved")
            seen_articles.add(art_id)
            last_article_id = art_id

        # Check text chunks before table chunks
        if c_type == "table":
            seen_table_for_article.add(art_id)
        elif c_type != "table":
            if art_id in seen_table_for_article:
                table_ordering_error_articles.add(art_id)
                errors.append(f"table_ordering_error: Text chunk of article {art_id} appears after table chunk")

    # Check table index non-decreasing for each article
    for art_id, art_chunks in article_chunks_map.items():
        last_tbl_idx = 0
        for chunk in art_chunks:
            if chunk.get("chunk_type") == "table":
                t_idx = chunk.get("table_index")
                if isinstance(t_idx, int):
                    if t_idx < last_tbl_idx:
                        table_ordering_error_articles.add(art_id)
                        errors.append(f"table_ordering_error: Table index decrements in article {art_id}")
                    last_tbl_idx = t_idx

    table_ordering_error_count = len(table_ordering_error_articles)

    # Detect mutation
    new_corpus_json = json.dumps(canonical_corpus, sort_keys=True, ensure_ascii=False)
    new_chunks_json = json.dumps(chunks, sort_keys=True, ensure_ascii=False)
    input_mutated = (new_corpus_json != orig_corpus_json) or (new_chunks_json != orig_chunks_json)
    input_chunk_count_preserved = (len(chunks) == orig_chunks_len)

    # Determine is_valid

    # PROMPT4B_UNKNOWN_TABLE_FIX_START
    # Tách riêng table IDs lạ khỏi tập table IDs canonical đã được cover.
    unknown_table_ids = sorted({
        table_id
        for chunk in chunks
        if chunk.get("chunk_type") == "table"
        for table_id in [chunk.get("table_id")]
        if table_id
        and table_id not in canonical_table_ids
    })
    unknown_table_count = len(unknown_table_ids)

    for table_id in unknown_table_ids:
        error_message = f"unknown_table: {table_id}"
        if error_message not in errors:
            errors.append(error_message)
    # PROMPT4B_UNKNOWN_TABLE_FIX_END

    is_valid = len(errors) == 0

    return {
        "is_valid": is_valid,
        "errors": sorted(errors),
        "warnings": sorted(warnings),

        "article_count": article_count,
        "chunk_count": chunk_count,

        "covered_article_count": len(covered_article_ids),
        "missing_article_ids": sorted(list(missing_article_ids)),

        "duplicate_chunk_id_count": duplicate_chunk_id_count,
        "duplicate_chunk_ids": sorted(list(duplicate_chunk_ids)),

        "duplicate_chunk_key_count": duplicate_chunk_key_count,
        "duplicate_chunk_keys": sorted(list(duplicate_chunk_keys)),

        "empty_content_count": empty_content_count,
        "empty_content_chunk_ids": sorted(empty_content_chunk_ids),

        "empty_body_count": empty_body_count,
        "empty_body_chunk_ids": sorted(empty_body_chunk_ids),

        "invalid_parent_count": invalid_parent_count,
        "invalid_parent_chunk_ids": sorted(invalid_parent_chunk_ids),
        "invalid_parent_article_ids": sorted(list(invalid_parent_article_ids)),

        "missing_required_field_count": missing_required_field_count,
        "chunks_missing_required_fields": sorted(chunks_missing_required_fields),

        "invalid_chunk_type_count": invalid_chunk_type_count,
        "invalid_chunk_type_chunks": sorted(invalid_chunk_type_chunks),

        "invalid_unit_type_count": invalid_unit_type_count,
        "invalid_unit_type_chunks": sorted(invalid_unit_type_chunks),

        "token_count_mismatch_count": token_count_mismatch_count,
        "token_count_mismatch_chunks": sorted(token_count_mismatch_chunks),

        "tokenizer_name_mismatch_count": tokenizer_name_mismatch_count,
        "tokenizer_name_mismatch_chunks": sorted(tokenizer_name_mismatch_chunks),

        "oversized_chunk_count": oversized_chunk_count,
        "oversized_chunks": sorted(oversized_chunks),
        "max_token_count": max_token_count,

        "requires_fallback_count": requires_fallback_count,
        "fallback_pending_chunks": sorted(fallback_pending_chunks),

        "stable_id_check": stable_id_check,
        "unstable_id_chunks": sorted(unstable_id_chunks),

        "JSON_serialization_error_count": JSON_serialization_error_count,
        "JSON_serialization_error_chunks": sorted(JSON_serialization_error_chunks),

        "relation_list_length_mismatch_count": relation_list_length_mismatch_count,
        "relation_list_length_mismatch_chunks": sorted(relation_list_length_mismatch_chunks),

        "canonical_non_table_unit_count": len(canonical_non_table_units),
        "covered_non_table_unit_count": len(covered_non_table_units),
        "missing_non_table_unit_count": len(missing_non_table_units),
        "missing_non_table_unit_ids": sorted(list(missing_non_table_units)),
        "unknown_source_unit_count": len(unknown_source_unit_ids),
        "unknown_source_unit_ids": sorted(list(unknown_source_unit_ids)),

        "canonical_table_count": len(canonical_table_ids),
        "covered_table_count": len(covered_table_ids),
        "missing_table_count": len(missing_table_ids),
        "missing_table_ids": sorted(list(missing_table_ids)),
        "unknown_table_count": len(unknown_table_ids),
        "unknown_table_ids": sorted(list(unknown_table_ids)),

        "duplicate_table_segment_key_count": duplicate_table_segment_key_count,
        "duplicate_table_segment_keys": sorted(list(duplicate_table_segment_keys)),

        "invalid_table_metadata_count": invalid_table_metadata_count,
        "invalid_table_metadata_chunks": sorted(invalid_table_metadata_chunks),

        "table_segment_sequence_error_count": table_segment_sequence_error_count,
        "table_segment_sequence_errors": sorted(table_segment_sequence_errors),

        "table_ordering_error_count": table_ordering_error_count,
        "table_ordering_error_articles": sorted(list(table_ordering_error_articles)),

        "input_chunk_count_preserved": input_chunk_count_preserved,
        "input_mutated": input_mutated,
    }


def _calculate_p95(values: list[int | float]) -> float:
    """
    Calculate the 95th percentile using the nearest-rank method.

    Formula:
        index = ceil(P / 100 * N) - 1
    where P = 95, N is the length of sorted list.
    """
    if not values:
        return 0.0
    import math
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    idx = math.ceil(0.95 * n) - 1
    idx = max(0, min(idx, n - 1))
    return float(sorted_vals[idx])


def build_chunking_summary(
    canonical_corpus: dict,
    chunks: list[dict],
    validation: dict,
    *,
    config: ChunkingConfig,
    token_counter: TokenCounter,
) -> dict:
    """
    Build a deterministic and JSON-serializable chunking summary of statistics and metrics.
    """
    meta = canonical_corpus.get("metadata") or {}
    first_art = canonical_corpus["articles"][0] if canonical_corpus.get("articles") else {}

    doc_id = meta.get("document_id") or first_art.get("document_id")
    topic_code = meta.get("topic_code") or first_art.get("topic_code")
    topic_name = meta.get("topic_name") or first_art.get("topic_name")
    parser_ver = meta.get("parser_version") or first_art.get("parser_version")
    source_sha = meta.get("source_sha256") or first_art.get("source_sha256")

    # Counts
    chunk_type_counts = {}
    unit_type_counts = {}
    source_type_counts = {}

    for c in chunks:
        ct = c.get("chunk_type")
        ut = c.get("unit_type")
        st = c.get("source_type")

        if ct is not None:
            chunk_type_counts[ct] = chunk_type_counts.get(ct, 0) + 1
        if ut is not None:
            unit_type_counts[ut] = unit_type_counts.get(ut, 0) + 1
        if st is not None:
            source_type_counts[st] = source_type_counts.get(st, 0) + 1

    chunk_type_counts = {k: chunk_type_counts[k] for k in sorted(chunk_type_counts.keys())}
    unit_type_counts = {k: unit_type_counts[k] for k in sorted(unit_type_counts.keys())}
    source_type_counts = {k: source_type_counts[k] for k in sorted(source_type_counts.keys())}

    # Token stats
    tokens = [c["token_count"] for c in chunks if isinstance(c.get("token_count"), (int, float)) and not isinstance(c.get("token_count"), bool)]
    if tokens:
        import statistics
        stat_min = min(tokens)
        stat_max = max(tokens)
        stat_total = sum(tokens)
        stat_mean = round(statistics.mean(tokens), 2)
        stat_median = round(statistics.median(tokens), 2)
        stat_p95 = round(_calculate_p95(tokens), 2)
    else:
        stat_min = 0
        stat_max = 0
        stat_total = 0
        stat_mean = 0.0
        stat_median = 0.0
        stat_p95 = 0.0

    return {
        "document_id": doc_id,
        "topic_code": topic_code,
        "topic_name": topic_name,
        "parser_version": parser_ver,
        "chunker_version": CHUNKER_VERSION,
        "source_sha256": source_sha,
        "article_count": len(canonical_corpus.get("articles") or []),
        "chunk_count": len(chunks),
        "tokenizer_name": token_counter.name,
        "config": {
            "target_tokens": config.target_tokens,
            "max_tokens": config.max_tokens,
            "fallback_overlap": config.fallback_overlap,
            "chunker_version": CHUNKER_VERSION
        },
        "chunk_type_counts": chunk_type_counts,
        "unit_type_counts": unit_type_counts,
        "source_type_counts": source_type_counts,
        "token_statistics": {
            "min": stat_min,
            "max": stat_max,
            "mean": stat_mean,
            "median": stat_median,
            "p95": stat_p95,
            "total": stat_total
        },
        "article_coverage": {
            "expected": validation["article_count"],
            "covered": validation["covered_article_count"],
            "missing_count": len(validation["missing_article_ids"]),
            "missing_ids": validation["missing_article_ids"]
        },
        "non_table_unit_coverage": {
            "expected": validation["canonical_non_table_unit_count"],
            "covered": validation["covered_non_table_unit_count"],
            "missing_count": validation["missing_non_table_unit_count"],
            "unknown_count": validation["unknown_source_unit_count"]
        },
        "table_coverage": {
            "expected": validation["canonical_table_count"],
            "covered": validation["covered_table_count"],
            "missing_count": validation["missing_table_count"],
            "unknown_count": validation["unknown_table_count"]
        },
        "fallback_statistics": {
            "fallback_segment_count": chunk_type_counts.get("fallback_segment", 0),
            "requires_fallback_count": validation["requires_fallback_count"]
        },
        "validation": {
            "is_valid": validation["is_valid"],
            "error_count": len(validation["errors"]),
            "warning_count": len(validation["warnings"])
        }
    }
