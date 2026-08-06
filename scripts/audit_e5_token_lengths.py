"""Deterministic, read-only audit of E5 token lengths for every legal chunk.

Measures the exact pre-truncation token length of every legal chunk using the
SAME tokenizer artifact loaded by FastEmbed 0.8.0 for
intfloat/multilingual-e5-large.

Run from the repository root:

    python -m scripts.audit_e5_token_lengths --dry-run
    python -m scripts.audit_e5_token_lengths --local-files-only
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("e5_token_audit")

# ---------------------------------------------------------------------------
# Optional heavy imports – loaded at module level so tests can monkeypatch.
# Both are None when fastembed is not installed; load_audit_tokenizer will
# raise a clear RuntimeError in that case.
# ---------------------------------------------------------------------------
try:
    from fastembed import TextEmbedding  # type: ignore[import-untyped]
    from fastembed.common.preprocessor_utils import (  # type: ignore[import-untyped]
        load_tokenizer,
    )
    from tokenizers import Tokenizer as _HFTokenizer  # type: ignore[import-untyped]
except ImportError:
    TextEmbedding = None  # type: ignore[assignment,misc]
    load_tokenizer = None  # type: ignore[assignment]
    _HFTokenizer = None  # type: ignore[assignment,misc]

E5_PREFIX = "passage: "
DEFAULT_CHUNKS = Path("data/processed/legal_chunks.jsonl")
DEFAULT_OUTPUT_DIR = Path("experiments/e5_token_audit")
DEFAULT_MODEL = "intfloat/multilingual-e5-large"
DEFAULT_EXPECTED_CHUNKS = 1127
DEFAULT_NEAR_LIMIT = 480
DEFAULT_EXPECTED_MAX_TOKENS = 512
DEFAULT_BATCH_SIZE = 128
DEFAULT_THREADS = 6


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AuditInputError(ValueError):
    """Raised when audit input is invalid or unsafe."""


# ---------------------------------------------------------------------------
# Data structures & Metadata Extraction
# ---------------------------------------------------------------------------


def make_content_preview(content: str | None) -> str:
    """Normalize consecutive whitespace to a single space, strip, and truncate to 240 chars."""
    if not content:
        return ""
    normalized = " ".join(content.split())
    if len(normalized) > 240:
        return normalized[:240].rstrip()
    return normalized


def extract_chunk_locator(chunk: dict[str, Any]) -> dict[str, Any]:
    """Extract metadata values with a fallback from top-level to nested metadata dictionary."""
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}

    def get_field(key: str) -> Any:
        if key in chunk:
            return chunk[key]
        return metadata.get(key)

    chunk_id = get_field("chunk_id")
    article_code = get_field("article_code")
    article_title = get_field("article_title")
    chunk_type = get_field("chunk_type")
    source_document_id = get_field("source_document_id")
    document_id = get_field("document_id")
    parent_document_id = get_field("parent_document_id")
    attachment_id = get_field("attachment_id")
    attachment_title = get_field("attachment_title")
    table_id = get_field("table_id")

    table_index_val = get_field("table_index")
    table_index = None
    if table_index_val is not None:
        try:
            table_index = int(table_index_val)
        except (ValueError, TypeError):
            table_index = table_index_val

    table_title = get_field("table_title")

    segment_index_val = get_field("segment_index")
    table_segment_index = None
    if segment_index_val is not None:
        try:
            table_segment_index = int(segment_index_val)
        except (ValueError, TypeError):
            table_segment_index = segment_index_val

    form_number = get_field("form_number")

    chunker_token_count_val = get_field("token_count")
    chunker_token_count = None
    if chunker_token_count_val is not None:
        try:
            chunker_token_count = int(chunker_token_count_val)
        except (ValueError, TypeError):
            chunker_token_count = chunker_token_count_val

    chunker_tokenizer = get_field("tokenizer_name")

    return {
        "chunk_id": chunk_id,
        "article_code": article_code,
        "article_title": article_title,
        "chunk_type": chunk_type,
        "source_document_id": source_document_id,
        "document_id": document_id,
        "parent_document_id": parent_document_id,
        "attachment_id": attachment_id,
        "attachment_title": attachment_title,
        "table_id": table_id,
        "table_index": table_index,
        "table_title": table_title,
        "table_segment_index": table_segment_index,
        "form_number": form_number,
        "chunker_token_count": chunker_token_count,
        "chunker_tokenizer": chunker_tokenizer,

        # Computed / Non-locator fields defaults
        "e5_content_token_count": 0,
        "e5_passage_token_count": 0,
        "prefix_overhead": 0,
        "model_max_tokens": 512,
        "overflow_tokens": 0,
        "near_limit": False,
        "strictly_over_limit": False,
        "content_preview": "",

        # Additional fields
        "source_document_title": get_field("source_document_title"),
        "row_start": get_field("row_start"),
        "row_end": get_field("row_end"),
        "structural_unit_ids": get_field("structural_unit_ids"),
    }


@dataclass
class ChunkRecord:
    """Token measurement for one chunk."""

    # Mandatory fields
    chunk_id: str
    article_code: str | None = None
    article_title: str | None = None
    chunk_type: str | None = None
    source_document_id: str | None = None
    document_id: str | None = None
    parent_document_id: str | None = None
    attachment_id: str | None = None
    attachment_title: str | None = None
    table_id: str | None = None
    table_index: int | None = None
    table_title: str | None = None
    table_segment_index: int | None = None
    form_number: str | None = None
    chunker_token_count: int | None = None
    chunker_tokenizer: str | None = None
    e5_content_token_count: int = 0
    e5_passage_token_count: int = 0
    prefix_overhead: int = 0
    model_max_tokens: int = 512
    overflow_tokens: int = 0
    near_limit: bool = False
    strictly_over_limit: bool = False
    content_preview: str = ""

    # Additional fields
    source_document_title: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    structural_unit_ids: list[str] | None = None

    # Keep compatibility with existing/old fields
    chunk_key: str | None = None
    container_type: str | None = None
    parent_article_id: str | None = None
    parent_attachment_id: str | None = None
    clause_number: str | None = None
    token_count_cl100k: int | None = None
    tokenizer_name_cl100k: str | None = None

    # Deprecated fields kept for backward compatibility (in case of other references)
    e5_prefix_overhead: int = 0
    near_or_above_limit: bool = False
    at_model_limit: bool = False
    over_model_limit: bool = False


def serialize_chunk_record(r: ChunkRecord) -> dict[str, Any]:
    """Serialize a ChunkRecord into a dictionary containing locator and token measurement fields."""
    return {
        "chunk_id": r.chunk_id,
        "article_code": r.article_code,
        "article_title": r.article_title,
        "chunk_type": r.chunk_type,
        "source_document_id": r.source_document_id,
        "document_id": r.document_id,
        "parent_document_id": r.parent_document_id,
        "attachment_id": r.attachment_id,
        "attachment_title": r.attachment_title,
        "table_id": r.table_id,
        "table_index": r.table_index,
        "table_title": r.table_title,
        "table_segment_index": r.table_segment_index,
        "form_number": r.form_number,
        "chunker_token_count": r.chunker_token_count,
        "chunker_tokenizer": r.chunker_tokenizer,
        "e5_content_token_count": r.e5_content_token_count,
        "e5_passage_token_count": r.e5_passage_token_count,
        "prefix_overhead": r.prefix_overhead,
        "model_max_tokens": r.model_max_tokens,
        "overflow_tokens": r.overflow_tokens,
        "near_limit": r.near_limit,
        "strictly_over_limit": r.strictly_over_limit,
        "content_preview": r.content_preview,

        # Additional fields
        "source_document_title": r.source_document_title,
        "row_start": r.row_start,
        "row_end": r.row_end,
        "structural_unit_ids": r.structural_unit_ids,
    }


@dataclass
class TokenizerAdapter:
    """Wraps two independent tokenizers for the audit.

    inference_tokenizer: the tokenizer returned by FastEmbed's load_tokenizer;
        used to verify max-length configuration only.
    audit_tokenizer: a clone with truncation/padding disabled; used for
        pre-truncation counts.
    """

    inference_tokenizer: Any  # tokenizers.Tokenizer
    audit_tokenizer: Any  # tokenizers.Tokenizer
    model_max_length: int
    tokenizer_class: str


@dataclass
class AuditStats:
    """Aggregate statistics over a list of integer counts."""

    min: int = 0
    max: int = 0
    mean: float = 0.0
    median: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    total: int = 0

    @staticmethod
    def from_counts(counts: list[int]) -> "AuditStats":
        if not counts:
            return AuditStats()
        s = sorted(counts)
        n = len(s)

        def percentile(p: float) -> float:
            idx = (p / 100) * (n - 1)
            lo, hi = int(idx), min(int(idx) + 1, n - 1)
            frac = idx - lo
            return s[lo] + frac * (s[hi] - s[lo])

        return AuditStats(
            min=s[0],
            max=s[-1],
            mean=round(statistics.fmean(s), 4),
            median=round(statistics.median(s), 4),
            p95=round(percentile(95), 4),
            p99=round(percentile(99), 4),
            total=sum(s),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "min": self.min,
            "max": self.max,
            "mean": self.mean,
            "median": self.median,
            "p95": self.p95,
            "p99": self.p99,
            "total": self.total,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure exact pre-truncation E5 token lengths for every legal chunk "
            "using the tokenizer loaded by FastEmbed 0.8.0."
        )
    )
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--expected-chunks", type=int, default=DEFAULT_EXPECTED_CHUNKS
    )
    parser.add_argument("--near-limit", type=int, default=DEFAULT_NEAR_LIMIT)
    parser.add_argument(
        "--expected-max-tokens", type=int, default=DEFAULT_EXPECTED_MAX_TOKENS
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument(
        "--local-files-only",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate input/configuration without loading FastEmbed or the "
            "tokenizer. Creates no output files."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Return exit code 2 after writing reports when any chunk exceeds the "
            "model token limit."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser


# ---------------------------------------------------------------------------
# Input validation (reuses load_chunks from test_sparse_dense)
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def validate_cli_args(args: argparse.Namespace) -> None:
    """Validate argument values before loading any model or data."""
    if args.near_limit <= 0:
        raise AuditInputError("--near-limit must be positive")
    if args.expected_max_tokens <= 0:
        raise AuditInputError("--expected-max-tokens must be positive")
    if args.near_limit > args.expected_max_tokens:
        raise AuditInputError(
            f"--near-limit ({args.near_limit}) must not exceed "
            f"--expected-max-tokens ({args.expected_max_tokens})"
        )
    if args.batch_size <= 0:
        raise AuditInputError("--batch-size must be positive")
    if args.threads <= 0:
        raise AuditInputError("--threads must be positive")
    if args.expected_chunks <= 0:
        raise AuditInputError("--expected-chunks must be positive")

    # Prevent output from colliding with input
    if args.output_dir.resolve() == args.chunks.parent.resolve():
        raise AuditInputError(
            "Output directory must differ from the chunk file's directory"
        )


# Reuse load_chunks from test_sparse_dense (validates IDs and content)
from scripts.test_sparse_dense import (  # noqa: E402
    ProbeInputError,
    e5_document_text,
    load_chunks,
)


def validate_chunk_count(
    chunks: list[dict[str, Any]], expected: int
) -> None:
    if len(chunks) != expected:
        raise AuditInputError(
            f"Expected {expected} chunks, found {len(chunks)}"
        )


# ---------------------------------------------------------------------------
# Tokenizer loading
# ---------------------------------------------------------------------------


def _resolve_model_dir(embedding: Any, model_name: str) -> Path:
    """Return the concrete model directory from a TextEmbedding wrapper."""
    inner = getattr(embedding, "model", None)
    if inner is None:
        raise RuntimeError(
            f"TextEmbedding for '{model_name}' has no '.model' attribute; "
            "FastEmbed private API may have changed."
        )
    model_dir = getattr(inner, "_model_dir", None)
    if model_dir is None:
        raise RuntimeError(
            f"Concrete model '{type(inner).__name__}' has no '_model_dir'; "
            "FastEmbed private API may have changed."
        )
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise RuntimeError(
            f"Model directory does not exist: {model_dir}. "
            "Run without --local-files-only to download the model."
        )
    return model_dir


def load_audit_tokenizer(
    model_name: str,
    threads: int,
    local_files_only: bool,
    cache_dir: Path | None,
    expected_max_tokens: int,
) -> TokenizerAdapter:
    """Load FastEmbed's tokenizer for the named model, then clone it with
    truncation/padding disabled for pre-truncation counting.

    Raises RuntimeError on any configuration mismatch.
    """
    if TextEmbedding is None or load_tokenizer is None or _HFTokenizer is None:
        raise RuntimeError(
            "Cannot import fastembed or tokenizers. "
            "Activate backend/.venv and install backend/requirements.txt."
        )
    Tokenizer = _HFTokenizer

    try:
        embedding_kwargs: dict[str, Any] = {
            "model_name": model_name,
            "threads": threads,
            "lazy_load": True,
            "local_files_only": local_files_only,
        }
        if cache_dir is not None:
            embedding_kwargs["cache_dir"] = str(cache_dir)

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            embedding = TextEmbedding(**embedding_kwargs)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to initialise TextEmbedding for '{model_name}': {exc}"
        ) from exc

    try:
        model_dir = _resolve_model_dir(embedding, model_name)
    except RuntimeError:
        raise

    try:
        inference_tokenizer, _ = load_tokenizer(model_dir)
    except Exception as exc:
        raise RuntimeError(
            f"FastEmbed load_tokenizer failed for {model_dir}: {exc}"
        ) from exc

    # Verify truncation is configured and max-length matches expectation
    trunc = inference_tokenizer.truncation
    if trunc is None:
        raise RuntimeError(
            f"Tokenizer at {model_dir} has no truncation metadata; "
            "check the tokenizer_config.json."
        )
    actual_max = trunc.get("max_length", None)
    if actual_max is None:
        raise RuntimeError(
            "Tokenizer truncation dict has no 'max_length' key."
        )
    if actual_max != expected_max_tokens:
        raise RuntimeError(
            f"Tokenizer max_length={actual_max} differs from "
            f"--expected-max-tokens={expected_max_tokens}. "
            "Pass the correct value or check the model."
        )

    # Clone with truncation/padding disabled for pre-truncation counts
    try:
        audit_tokenizer = Tokenizer.from_str(inference_tokenizer.to_str())
        audit_tokenizer.no_truncation()
        audit_tokenizer.no_padding()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to clone tokenizer: {exc}"
        ) from exc

    tokenizer_class = type(inference_tokenizer).__name__

    return TokenizerAdapter(
        inference_tokenizer=inference_tokenizer,
        audit_tokenizer=audit_tokenizer,
        model_max_length=actual_max,
        tokenizer_class=tokenizer_class,
    )


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def count_tokens_batch(
    texts: list[str],
    adapter: TokenizerAdapter,
    add_special_tokens: bool = True,
) -> list[int]:
    """Encode a batch of texts and return token counts.

    Uses the audit tokenizer (no truncation, no padding).
    add_special_tokens=True is the default for both content and passage counts;
    the tokenizers library respects special-token configuration from the model
    files regardless of this flag name.
    """
    encodings = adapter.audit_tokenizer.encode_batch(
        texts, add_special_tokens=add_special_tokens
    )
    return [len(enc.ids) for enc in encodings]


def measure_chunks(
    chunks: list[dict[str, Any]],
    adapter: TokenizerAdapter,
    model_name: str,
    near_limit: int,
    batch_size: int,
) -> list[ChunkRecord]:
    """Measure every chunk and return ChunkRecord list in input order."""
    model_max = adapter.model_max_length
    records: list[ChunkRecord] = []

    total = len(chunks)
    for start in range(0, total, batch_size):
        batch = chunks[start : start + batch_size]
        content_texts = [c["content"] for c in batch]
        passage_texts = [
            e5_document_text(c["content"], model_name) for c in batch
        ]

        content_counts = count_tokens_batch(content_texts, adapter)
        passage_counts = count_tokens_batch(passage_texts, adapter)

        for chunk, content_cnt, passage_cnt in zip(
            batch, content_counts, passage_counts, strict=True
        ):
            if content_cnt <= 0 or passage_cnt <= 0:
                raise AuditInputError(
                    f"Chunk {chunk['chunk_id']} produced a zero/negative "
                    f"token count (content={content_cnt}, "
                    f"passage={passage_cnt}). Check the tokenizer."
                )
            overflow = max(passage_cnt - model_max, 0)

            # Extract locator and build content preview
            locator = extract_chunk_locator(chunk)
            preview = make_content_preview(chunk.get("content"))

            # Fallback/extract optional fields
            source_document_title = chunk.get("source_document_title")
            row_start = chunk.get("row_start")
            row_end = chunk.get("row_end")
            structural_unit_ids = chunk.get("structural_unit_ids")

            records.append(ChunkRecord(
                chunk_id=chunk["chunk_id"],
                chunk_key=chunk.get("chunk_key"),
                article_code=locator["article_code"],
                article_title=locator["article_title"],
                chunk_type=locator["chunk_type"],
                container_type=chunk.get("container_type"),
                parent_article_id=chunk.get("parent_article_id"),
                parent_attachment_id=chunk.get("parent_attachment_id"),
                clause_number=str(chunk["clause_number"])
                if chunk.get("clause_number") is not None
                else None,
                token_count_cl100k=chunk.get("token_count"),
                tokenizer_name_cl100k=chunk.get("tokenizer_name"),
                e5_content_token_count=content_cnt,
                e5_passage_token_count=passage_cnt,
                e5_prefix_overhead=passage_cnt - content_cnt,
                near_or_above_limit=passage_cnt >= near_limit,
                at_model_limit=passage_cnt == model_max,
                over_model_limit=passage_cnt > model_max,
                overflow_tokens=overflow,

                # Extended metadata
                source_document_id=locator["source_document_id"],
                document_id=locator["document_id"],
                parent_document_id=locator["parent_document_id"],
                source_document_title=source_document_title,
                attachment_id=locator["attachment_id"],
                attachment_title=locator["attachment_title"],
                table_id=locator["table_id"],
                table_index=locator["table_index"],
                table_title=locator["table_title"],
                table_segment_index=locator["table_segment_index"],
                form_number=locator["form_number"],
                row_start=row_start,
                row_end=row_end,
                structural_unit_ids=structural_unit_ids,
                chunker_token_count=locator["chunker_token_count"],
                chunker_tokenizer=locator["chunker_tokenizer"],
                model_max_tokens=model_max,
                prefix_overhead=passage_cnt - content_cnt,
                near_limit=passage_cnt >= near_limit,
                strictly_over_limit=passage_cnt > model_max,
                content_preview=preview,
            ))
        LOGGER.info(
            "Measured %d/%d chunks",
            min(start + batch_size, total),
            total,
        )

    return records


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 8)

def build_tokenizer_comparison(
    records: list[ChunkRecord],
    model_max_tokens: int,
) -> dict[str, Any]:
    """Build a comparison object between E5 and cl100k tokenizers, including confusion matrix counts."""
    # Extract records with a valid non-negative integer chunker_token_count
    valid_records = []
    for r in records:
        val = r.chunker_token_count
        if val is not None:
            try:
                val_int = int(val)
                if val_int >= 0:
                    valid_records.append((r, val_int))
            except (ValueError, TypeError):
                pass

    available_count = len(valid_records)
    missing_count = len(records) - available_count

    # source_tokenizers: sorted deduplicated list of non-empty tokenizer names from valid records
    tokenizers = set()
    for r, _ in valid_records:
        tok_name = r.chunker_tokenizer
        if tok_name and str(tok_name).strip():
            tokenizers.add(str(tok_name).strip())
    source_tokenizers = sorted(list(tokenizers))

    # Confusion matrix and counts
    true_positive_count = 0
    true_negative_count = 0
    false_positive_count = 0
    false_negative_count = 0
    proxy_at_or_above_limit_count = 0
    exact_strictly_over_limit_count = 0

    diffs = []
    for r, cl_val in valid_records:
        e5_val = r.e5_passage_token_count
        diff = e5_val - cl_val
        diffs.append(diff)

        proxy_pos = cl_val >= model_max_tokens
        exact_pos = e5_val > model_max_tokens

        if proxy_pos:
            proxy_at_or_above_limit_count += 1
        if exact_pos:
            exact_strictly_over_limit_count += 1

        if proxy_pos and exact_pos:
            true_positive_count += 1
        elif not proxy_pos and not exact_pos:
            true_negative_count += 1
        elif proxy_pos and not exact_pos:
            false_positive_count += 1
        elif not proxy_pos and exact_pos:
            false_negative_count += 1

    # Difference statistics
    if available_count > 0:
        s = sorted(diffs)
        n = len(s)

        def percentile(p: float) -> float:
            idx = (p / 100) * (n - 1)
            lo, hi = int(idx), min(int(idx) + 1, n - 1)
            frac = idx - lo
            return s[lo] + frac * (s[hi] - s[lo])

        diff_stats = {
            "count": available_count,
            "min": s[0],
            "max": s[-1],
            "mean": round(statistics.fmean(s), 4),
            "median": round(statistics.median(s), 4),
            "p95": round(percentile(95), 4),
        }
    else:
        diff_stats = {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "p95": None,
        }

    return {
        "available_count": available_count,
        "missing_count": missing_count,
        "source_tokenizers": source_tokenizers,
        "difference_definition": "e5_passage_token_count - chunker_token_count",
        "difference_statistics": diff_stats,
        "proxy_at_or_above_limit_count": proxy_at_or_above_limit_count,
        "exact_strictly_over_limit_count": exact_strictly_over_limit_count,
        "true_positive_count": true_positive_count,
        "true_negative_count": true_negative_count,
        "false_positive_count": false_positive_count,
        "false_negative_count": false_negative_count,
    }


def build_summary(
    records: list[ChunkRecord],
    chunks: list[dict[str, Any]],
    input_path: Path,
    input_sha256: str,
    model_name: str,
    adapter: TokenizerAdapter,
    near_limit: int,
) -> dict[str, Any]:
    model_max = adapter.model_max_length

    passage_counts = [r.e5_passage_token_count for r in records]
    content_counts = [r.e5_content_token_count for r in records]
    overhead_counts = [r.e5_prefix_overhead for r in records]

    near_or_above = sum(r.near_or_above_limit for r in records)
    at_limit = sum(r.at_model_limit for r in records)
    strictly_over = sum(r.over_model_limit for r in records)

    # Groupings
    def _count_by(key: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for r in records:
            v = str(getattr(r, key) or "")
            result[v] = result.get(v, 0) + 1
        return dict(sorted(result.items()))

    def _risk_by(key: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for r in records:
            if r.near_or_above_limit:
                v = str(getattr(r, key) or "")
                result[v] = result.get(v, 0) + 1
        return dict(sorted(result.items()))

    # Top 20 longest
    top20 = sorted(
        records, key=lambda r: (-r.e5_passage_token_count, r.chunk_id)
    )[:20]

    # cl100k comparison
    cl_comparison = build_tokenizer_comparison(records, model_max)

    # Validation block
    error_count = strictly_over
    warning_count = near_or_above - at_limit - strictly_over
    is_valid = error_count == 0

    try:
        import fastembed
        fastembed_version = fastembed.__version__
    except Exception:
        fastembed_version = "unknown"

    return {
        "status": "completed",
        "input_path": str(input_path),
        "input_sha256": input_sha256,
        "chunk_count": len(records),
        "model_name": model_name,
        "fastembed_version": fastembed_version,
        "tokenizer_class": adapter.tokenizer_class,
        "actual_model_max_tokens": model_max,
        "prefix_string": E5_PREFIX,
        "near_limit_threshold": near_limit,
        "exact_measurement": True,
        "token_statistics": AuditStats.from_counts(passage_counts).to_dict(),
        "content_token_statistics": AuditStats.from_counts(
            content_counts
        ).to_dict(),
        "prefix_overhead_statistics": AuditStats.from_counts(
            overhead_counts
        ).to_dict(),
        "risk_counts": {
            "near_or_above_count": near_or_above,
            "at_model_limit_count": at_limit,
            "strictly_over_model_limit_count": strictly_over,
        },
        "counts_by_chunk_type": _count_by("chunk_type"),
        "risk_counts_by_chunk_type": _risk_by("chunk_type"),
        "counts_by_container_type": _count_by("container_type"),
        "risk_counts_by_container_type": _risk_by("container_type"),
        "top_20_longest": [
            {
                "chunk_id": r.chunk_id,
                "article_code": r.article_code,
                "chunk_type": r.chunk_type,
                "e5_passage_token_count": r.e5_passage_token_count,
                "overflow_tokens": r.overflow_tokens,
            }
            for r in top20
        ],
        "cl100k_comparison": cl_comparison,
        "comparison_against_cl100k": cl_comparison,
        "validation": {
            "error_count": error_count,
            "warning_count": warning_count,
            "is_valid": is_valid,
        },
    }


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------

_CSV_FIELDS = [
    "chunk_id",
    "article_code",
    "article_title",
    "chunk_type",
    "source_document_id",
    "document_id",
    "parent_document_id",
    "attachment_id",
    "attachment_title",
    "table_id",
    "table_index",
    "table_title",
    "table_segment_index",
    "form_number",
    "chunker_token_count",
    "chunker_tokenizer",
    "e5_content_token_count",
    "e5_passage_token_count",
    "prefix_overhead",
    "model_max_tokens",
    "overflow_tokens",
    "near_limit",
    "strictly_over_limit",
    "content_preview",
    "source_document_title",
    "row_start",
    "row_end",
    "structural_unit_ids",
]


def write_outputs(
    records: list[ChunkRecord],
    summary: dict[str, Any],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    LOGGER.info("Wrote %s", summary_path)

    csv_path = output_dir / "chunks.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for r in records:
            writer.writerow(serialize_chunk_record(r))
    LOGGER.info("Wrote %s", csv_path)

    over_limit = [
        r for r in records if r.strictly_over_limit
    ]
    over_limit_sorted = sorted(
        over_limit, key=lambda r: (-r.e5_passage_token_count, r.chunk_id)
    )
    over_path = output_dir / "over_limit.json"
    over_payload = [serialize_chunk_record(r) for r in over_limit_sorted]
    over_path.write_text(
        json.dumps(over_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    LOGGER.info("Wrote %s", over_path)

    near_limit_records = [
        r for r in records if r.near_limit
    ]
    near_limit_sorted = sorted(
        near_limit_records, key=lambda r: (-r.e5_passage_token_count, r.chunk_id)
    )
    near_path = output_dir / "near_limit.json"
    near_payload = [serialize_chunk_record(r) for r in near_limit_sorted]
    near_path.write_text(
        json.dumps(near_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    LOGGER.info("Wrote %s", near_path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_cli_args(args)

    try:
        chunks = load_chunks(args.chunks)
    except ProbeInputError as exc:
        raise AuditInputError(str(exc)) from exc

    validate_chunk_count(chunks, args.expected_chunks)
    input_sha = _sha256(args.chunks)

    if args.dry_run:
        return {
            "status": "dry_run_pass",
            "input_path": str(args.chunks),
            "input_sha256": input_sha,
            "chunk_count": len(chunks),
            "model_name": args.model,
            "near_limit": args.near_limit,
            "expected_max_tokens": args.expected_max_tokens,
            "local_files_only": args.local_files_only,
        }

    LOGGER.info(
        "Loading FastEmbed tokenizer for %s (lazy_load=True)", args.model
    )
    adapter = load_audit_tokenizer(
        model_name=args.model,
        threads=args.threads,
        local_files_only=args.local_files_only,
        cache_dir=args.cache_dir,
        expected_max_tokens=args.expected_max_tokens,
    )
    LOGGER.info(
        "Tokenizer loaded: %s, max_length=%d",
        adapter.tokenizer_class,
        adapter.model_max_length,
    )

    records = measure_chunks(
        chunks=chunks,
        adapter=adapter,
        model_name=args.model,
        near_limit=args.near_limit,
        batch_size=args.batch_size,
    )

    summary = build_summary(
        records=records,
        chunks=chunks,
        input_path=args.chunks,
        input_sha256=input_sha,
        model_name=args.model,
        adapter=adapter,
        near_limit=args.near_limit,
    )

    write_outputs(records, summary, args.output_dir)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        report = run(args)
    except (AuditInputError, RuntimeError, OSError) as exc:
        LOGGER.error("%s", exc)
        return 2

    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    else:
        strictly_over = report["validation"]["error_count"]
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "chunk_count": report["chunk_count"],
                    "strictly_over_model_limit": strictly_over,
                    "near_or_above_count": report["risk_counts"][
                        "near_or_above_count"
                    ],
                    "output_dir": str(args.output_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if strictly_over > 0:
            LOGGER.warning(
                "%d chunk(s) exceed the model max tokens. "
                "Stop production indexing and review over_limit.json.",
                strictly_over,
            )
            if args.strict:
                return 2
        else:
            LOGGER.info(
                "No over-limit chunks. Corpus may proceed to production "
                "indexing."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
