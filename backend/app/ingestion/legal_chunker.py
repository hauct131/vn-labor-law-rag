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
    body_text: str,
    source_unit_ids: list[str],
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
        "table_id": None,
        "table_index": None,
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
            # không tạo chunk cho article không có text unit
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
            initial_chunks.append(chunk)

            # Verification: coverage check
            _verify_coverage(art_id, text_units, [chunk])
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
            # article_long không có clause
            art_chunk = _create_chunk(
                article,
                chunk_type="fallback_segment",
                unit_type="article",
                segment_index=1,
                body_text=art_body,
                source_unit_ids=art_source_ids,
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
        initial_chunks.extend(article_chunks)

    # Phase 2: Fallback splitting for chunks that require fallback
    final_chunks = []
    for chunk in initial_chunks:
        if not chunk.get("requires_fallback"):
            chunk["fallback_source_reason"] = None
            final_chunks.append(chunk)
            continue

        # Extract information from oversized chunk
        orig_reason = chunk.get("oversized_reason")
        body = chunk.get("body_text", "")
        article_id = chunk.get("parent_article_id")
        orig_key = chunk.get("chunk_key")

        parent_art = next((art for art in canonical_corpus["articles"] if art["article_id"] == article_id), None)
        if not parent_art:
            raise ValueError(f"Parent article {article_id} not found in corpus")

        # Split using RecursiveCharacterTextSplitter helper
        body_segments = split_oversized_legal_text(body, config=cfg, token_counter=tc)
        if not body_segments:
            continue

        repaired_segments = []
        for seg in body_segments:
            # Rebuild content for test
            test_content = _build_chunk_content(
                parent_art,
                body_text=seg,
                clause_number=chunk.get("clause_number"),
                point_labels=chunk.get("point_labels")
            )
            if tc.count(test_content) <= cfg.max_tokens:
                repaired_segments.append((seg, False, None, []))
            else:
                # Need repair because segment content exceeds max_tokens
                empty_body_content = _build_chunk_content(
                    parent_art,
                    body_text="",
                    clause_number=chunk.get("clause_number"),
                    point_labels=chunk.get("point_labels")
                )
                breadcrumb_tokens = tc.count(empty_body_content)
                available_body_tokens = cfg.max_tokens - breadcrumb_tokens

                if available_body_tokens <= cfg.fallback_overlap:
                    raise ValueError(
                        f"Cannot split text for article {article_id} (key {orig_key}): "
                        f"breadcrumb tokens ({breadcrumb_tokens}) leave too few tokens "
                        f"for body under max_tokens ({cfg.max_tokens}) and overlap ({cfg.fallback_overlap})."
                    )

                repair_size = available_body_tokens
                repair_overlap = min(cfg.fallback_overlap, max(0, available_body_tokens - 1))

                repair_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=repair_size,
                    chunk_overlap=repair_overlap,
                    length_function=tc.count,
                    separators=[
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
                    ],
                    keep_separator=True
                )
                raw_repair_segs = repair_splitter.split_text(seg)
                repair_segs = [s.strip() for s in raw_repair_segs if s.strip()]

                for r_seg in repair_segs:
                    r_content = _build_chunk_content(
                        parent_art,
                        body_text=r_seg,
                        clause_number=chunk.get("clause_number"),
                        point_labels=chunk.get("point_labels")
                    )
                    if tc.count(r_content) <= cfg.max_tokens:
                        repaired_segments.append((r_seg, False, None, []))
                    else:
                        repaired_segments.append((
                            r_seg,
                            True,
                            "repair_failed",
                            ["fallback_segment_still_oversized"]
                        ))

        # Re-create fallback segments chunks
        for s_idx, (seg_body, req_fb, r_reason, r_warnings) in enumerate(repaired_segments, 1):
            seg_chunk = _create_chunk(
                parent_art,
                chunk_type="fallback_segment",
                unit_type=chunk.get("unit_type"),
                clause_number=chunk.get("clause_number"),
                clause_occurrence=chunk.get("clause_occurrence"),
                point_labels=chunk.get("point_labels"),
                point_occurrences=chunk.get("point_occurrences"),
                segment_index=s_idx,
                body_text=seg_body,
                source_unit_ids=chunk.get("source_unit_ids"),
                token_counter=tc,
                requires_fallback=req_fb,
                oversized_reason=r_reason,
                warnings=list(set(chunk.get("warnings", []) + r_warnings)),
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
        all_output_unit_ids.update(chunk["source_unit_ids"])

    missing_ids = all_input_unit_ids - all_output_unit_ids
    if missing_ids:
        raise ValueError(
            f"Data loss detected in final output: non-table unit_ids {missing_ids} are not covered by any chunk."
        )

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
