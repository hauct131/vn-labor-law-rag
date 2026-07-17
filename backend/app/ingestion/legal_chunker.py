"""Structural chunking theo Điều → Khoản → Điểm."""

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid5

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


def build_legal_chunks(records: list[dict]) -> list[dict]:
    """Stub for legal chunker implementation."""
    raise NotImplementedError("Sẽ triển khai ở Ngày 3.")
