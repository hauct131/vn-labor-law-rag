"""CLI: Build legal chunks from canonical corpus JSON.

Usage:
    python scripts/build_chunks.py [OPTIONS]

This script is pure orchestration — all chunking, validation, and summary
logic lives in backend.app.ingestion.legal_chunker.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

# Support both ``python -m scripts.build_chunks`` and direct execution via
# ``python scripts/build_chunks.py`` from the repository root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.ingestion.legal_chunker import (
    ChunkingConfig,
    RegexEstimatedTokenCounter,
    TiktokenTokenCounter,
    build_chunking_summary,
    build_legal_chunks,
    validate_chunks,
)

# ---------------------------------------------------------------------------
# NOTE: No top-level side-effects.  All logic is inside functions.
# ---------------------------------------------------------------------------


class CliInputError(Exception):
    """Raised by helper functions for user-facing input/config errors.

    main() catches this and returns exit code 2.
    Helpers must NOT raise SystemExit directly.
    """



def build_parser() -> argparse.ArgumentParser:
    """Return the configured ArgumentParser (importable without side-effects)."""
    p = argparse.ArgumentParser(
        prog="build_chunks",
        description="Build structural legal chunks from a canonical corpus JSON file.",
    )
    p.add_argument(
        "--input",
        default="data/processed/articles_raw.json",
        metavar="PATH",
        help="Path to canonical corpus JSON (default: data/processed/articles_raw.json)",
    )
    p.add_argument(
        "--output",
        default="data/processed/legal_chunks.jsonl",
        metavar="PATH",
        help="Output JSONL path (default: data/processed/legal_chunks.jsonl)",
    )
    p.add_argument(
        "--summary-output",
        default="data/processed/chunking_summary.json",
        metavar="PATH",
        help="Output summary JSON path (default: data/processed/chunking_summary.json)",
    )
    p.add_argument(
        "--target-tokens",
        type=int,
        default=500,
        metavar="N",
        help="Target chunk size in tokens (default: 500)",
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=750,
        metavar="N",
        help="Maximum chunk size in tokens (default: 750)",
    )
    p.add_argument(
        "--overlap",
        type=int,
        default=80,
        metavar="N",
        help="Overlap tokens for fallback splitter (default: 80)",
    )
    p.add_argument(
        "--tokenizer",
        default="cl100k_base",
        metavar="NAME",
        help="Tokenizer name: cl100k_base | regex-estimate-v1 (default: cl100k_base)",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Fail and do not write output when validation errors exist",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        metavar="LEVEL",
        help="Logging level (default: INFO)",
    )
    return p


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _resolve_token_counter(tokenizer_name: str):
    """Return a TokenCounter for *tokenizer_name* or raise CliInputError."""
    if tokenizer_name == "cl100k_base":
        return TiktokenTokenCounter("cl100k_base")
    elif tokenizer_name == "regex-estimate-v1":
        return RegexEstimatedTokenCounter()
    else:
        raise CliInputError(
            f"Unsupported tokenizer '{tokenizer_name}'. "
            "Valid values: cl100k_base, regex-estimate-v1"
        )


def _load_corpus(input_path: Path) -> dict:
    """Read and validate canonical corpus; raise CliInputError on any user error."""
    if not input_path.exists():
        raise CliInputError(f"Input file not found: {input_path}")
    if not input_path.is_file():
        raise CliInputError(f"Input path is not a file: {input_path}")

    try:
        raw = input_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliInputError(f"Cannot read input file {input_path}: {exc}") from exc

    try:
        corpus = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliInputError(
            f"Input file is not valid JSON ({input_path}): {exc}"
        ) from exc

    if not isinstance(corpus, dict):
        raise CliInputError(
            f"Top-level JSON must be a dict, got {type(corpus).__name__}: {input_path}"
        )

    metadata = corpus.get("metadata")
    if not isinstance(metadata, dict):
        raise CliInputError(f"'metadata' must be a dict in {input_path}")

    articles = corpus.get("articles")
    if not isinstance(articles, list):
        raise CliInputError(f"'articles' must be a list in {input_path}")

    return corpus


def _check_path_conflicts(input_path: Path, output_path: Path, summary_path: Path) -> None:
    """Fail fast on path conflicts; raise CliInputError."""
    if output_path.resolve() == summary_path.resolve():
        raise CliInputError(
            f"Output and summary-output cannot be the same path: {output_path}"
        )
    if input_path.resolve() == output_path.resolve():
        raise CliInputError(
            f"Input and output cannot be the same path: {input_path}"
        )
    if input_path.resolve() == summary_path.resolve():
        raise CliInputError(
            f"Input and summary-output cannot be the same path: {input_path}"
        )


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically (within the same directory).

    Steps:
        1. Create parent directory if needed.
        2. Write to a named temp file in the same directory.
        3. flush + fsync.
        4. os.replace(temp → path).
        5. Clean up temp file on any error.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".tmp_" + path.name + "_",
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _serialize_jsonl(chunks: list[dict]) -> str:
    """Serialize chunks to JSONL string (one chunk per line, trailing newline)."""
    lines = [
        json.dumps(chunk, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for chunk in chunks
    ]
    return "\n".join(lines) + "\n"


def _serialize_summary(summary: dict) -> str:
    """Serialize summary dict to pretty JSON string (trailing newline)."""
    return json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Entry point.  Always returns int — never raises SystemExit."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    log = logging.getLogger(__name__)

    # Resolve paths
    input_path = Path(args.input)
    output_path = Path(args.output)
    summary_path = Path(getattr(args, "summary_output"))

    log.debug("Input: %s", input_path)
    log.debug("Output: %s", output_path)
    log.debug("Summary: %s", summary_path)

    # -----------------------------------------------------------------------
    # 1. Path conflict check
    # -----------------------------------------------------------------------
    try:
        _check_path_conflicts(input_path, output_path, summary_path)
    except CliInputError as exc:
        log.error("%s", exc)
        return 2

    # -----------------------------------------------------------------------
    # 2. Load and validate corpus
    # -----------------------------------------------------------------------
    log.info("Reading corpus from %s", input_path)
    try:
        corpus = _load_corpus(input_path)
    except CliInputError as exc:
        log.error("%s", exc)
        return 2
    article_count = len(corpus["articles"])
    log.info("Articles: %d", article_count)

    # -----------------------------------------------------------------------
    # 3. Build token counter
    # -----------------------------------------------------------------------
    try:
        token_counter = _resolve_token_counter(args.tokenizer)
    except CliInputError as exc:
        log.error("%s", exc)
        return 2
    log.debug("Tokenizer: %s", token_counter.name)

    # -----------------------------------------------------------------------
    # 4. Build chunking config
    # -----------------------------------------------------------------------
    try:
        cfg = ChunkingConfig(
            target_tokens=args.target_tokens,
            max_tokens=args.max_tokens,
            fallback_overlap=args.overlap,
        )
    except (ValueError, TypeError) as exc:
        log.error("Invalid chunking config: %s", exc)
        return 2

    # -----------------------------------------------------------------------
    # 5. Build chunks
    # -----------------------------------------------------------------------
    log.info("Building chunks…")
    try:
        chunks = build_legal_chunks(corpus, config=cfg, token_counter=token_counter)
    except Exception as exc:
        log.error("build_legal_chunks failed: %s", exc)
        log.debug("Traceback", exc_info=True)
        return 1

    log.info("Chunks built: %d", len(chunks))

    # -----------------------------------------------------------------------
    # 6. Validate
    # -----------------------------------------------------------------------
    log.info("Validating chunks…")
    try:
        validation = validate_chunks(corpus, chunks, config=cfg, token_counter=token_counter)
    except Exception as exc:
        log.error("validate_chunks failed: %s", exc)
        log.debug("Traceback", exc_info=True)
        return 1

    # -----------------------------------------------------------------------
    # 7. Strict mode check
    # -----------------------------------------------------------------------
    if not validation["is_valid"]:
        error_count = len(validation["errors"])
        log.warning(
            "Validation FAILED with %d error(s). First errors:", error_count
        )
        for err in validation["errors"][:5]:
            log.warning("  %s", err)

        if args.strict:
            log.error(
                "--strict mode: aborting.  Output files NOT written."
            )
            return 1
        else:
            log.warning(
                "Continuing despite validation errors (non-strict mode). "
                "Summary will reflect validation status."
            )
    else:
        log.info("Validation: PASS (0 errors)")

    # -----------------------------------------------------------------------
    # 8. Build summary
    # -----------------------------------------------------------------------
    log.info("Building chunking summary…")
    try:
        summary = build_chunking_summary(
            corpus, chunks, validation, config=cfg, token_counter=token_counter
        )
    except Exception as exc:
        log.error("build_chunking_summary failed: %s", exc)
        log.debug("Traceback", exc_info=True)
        return 1

    # -----------------------------------------------------------------------
    # 9. Serialize
    # -----------------------------------------------------------------------
    log.debug("Serializing JSONL…")
    jsonl_text = _serialize_jsonl(chunks)

    log.debug("Serializing summary…")
    summary_text = _serialize_summary(summary)

    # -----------------------------------------------------------------------
    # 10. Atomic write
    # -----------------------------------------------------------------------
    log.info("Writing JSONL to %s", output_path)
    try:
        _atomic_write_text(output_path, jsonl_text)
    except Exception as exc:
        log.error("Failed to write JSONL: %s", exc)
        log.debug("Traceback", exc_info=True)
        return 1

    log.info("Writing summary to %s", summary_path)
    try:
        _atomic_write_text(summary_path, summary_text)
    except Exception as exc:
        log.error("Failed to write summary: %s", exc)
        log.debug("Traceback", exc_info=True)
        return 1

    # -----------------------------------------------------------------------
    # 11. Final report
    # -----------------------------------------------------------------------
    log.info(
        "Done. input=%s articles=%d chunks=%d output=%s summary=%s validation=%s",
        input_path,
        article_count,
        len(chunks),
        output_path,
        summary_path,
        "PASS" if validation["is_valid"] else "FAIL",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
