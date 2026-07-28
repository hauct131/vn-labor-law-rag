"""Production-safe Qdrant Indexer for Vietnamese Labor Law Legal Chunks.

Indexes legal chunks into a Qdrant collection with named dense vectors
(multilingual-e5-large) and named sparse vectors (BM25).

Features strict preflight checks, corpus SHA-256 fingerprinting, E5 audit verification,
idempotent resume, and post-indexing validation.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from backend.app.core.config import settings

LOGGER = logging.getLogger("qdrant_production_indexer")

DEFAULT_CHUNKS = Path(settings.legal_chunks_path)
DEFAULT_AUDIT_SUMMARY = Path(settings.e5_audit_summary_path)
DEFAULT_SUMMARY_OUTPUT = Path("data/processed/qdrant_index/summary.json")
DEFAULT_EXPECTED_CHUNKS = settings.retrieval_expected_chunks
DEFAULT_EXPECTED_SHA256 = settings.retrieval_corpus_sha256
PROBE_COLLECTION = "labor_law_model_probe"
INDEXER_VERSION = "1.1.0"


class IndexerError(ValueError):
    """Raised when indexer configuration, preflight check, or operation fails."""


def is_valid_uuid(val: Any) -> bool:
    """Check if val is a valid UUID string."""
    if not isinstance(val, str):
        return False
    try:
        uuid.UUID(val)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def compute_sha256(path: Path) -> str:
    """Calculate SHA-256 hash of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def load_and_validate_chunks(
    path: Path,
    expected_chunks: int | None = None,
    expected_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Load JSONL legal chunks and perform strict validation."""
    if not path.is_file():
        raise IndexerError(f"Corpus chunk file not found: {path}")

    actual_sha256 = compute_sha256(path)
    if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
        raise IndexerError(
            f"Corpus SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )

    chunks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                chunk = json.loads(line_str)
            except json.JSONDecodeError as exc:
                raise IndexerError(
                    f"Invalid JSON at {path}:{line_no}: {exc}"
                ) from exc

            if not isinstance(chunk, dict):
                raise IndexerError(
                    f"Line {line_no} in {path} must be a JSON object"
                )

            chunk_id = chunk.get("chunk_id")
            if not is_valid_uuid(chunk_id):
                raise IndexerError(
                    f"Line {line_no} in {path} has invalid or missing UUID chunk_id: {chunk_id}"
                )

            if chunk_id in seen_ids:
                raise IndexerError(
                    f"Duplicate chunk_id found at line {line_no}: {chunk_id}"
                )

            content = chunk.get("content")
            if not isinstance(content, str) or not content.strip():
                raise IndexerError(
                    f"Chunk {chunk_id} at line {line_no} has empty content"
                )

            # Ensure serializable
            try:
                json.dumps(chunk)
            except (TypeError, ValueError) as exc:
                raise IndexerError(
                    f"Chunk {chunk_id} is not JSON-serializable: {exc}"
                ) from exc

            seen_ids.add(chunk_id)
            chunks.append(chunk)

    if not chunks:
        raise IndexerError(f"No valid chunks found in {path}")

    if expected_chunks is not None and len(chunks) != expected_chunks:
        raise IndexerError(
            f"Chunk count mismatch: expected {expected_chunks}, found {len(chunks)}"
        )

    return chunks, actual_sha256


def validate_audit_summary(
    audit_path: Path,
    expected_sha256: str,
    dense_model: str,
    expected_chunks: int,
) -> dict[str, Any]:
    """Verify strict E5 audit summary before proceeding."""
    if not audit_path.is_file():
        raise IndexerError(f"E5 audit summary file not found: {audit_path}")

    try:
        summary = json.loads(audit_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IndexerError(
            f"Invalid JSON in E5 audit summary {audit_path}: {exc}"
        ) from exc

    if not isinstance(summary, dict):
        raise IndexerError(f"Audit summary in {audit_path} must be a JSON object")

    audit_sha = summary.get("input_sha256")
    if not audit_sha or audit_sha.lower() != expected_sha256.lower():
        raise IndexerError(
            f"Audit summary input_sha256 mismatch: expected {expected_sha256}, got {audit_sha}"
        )

    audit_model = summary.get("model_name")
    if audit_model != dense_model:
        raise IndexerError(
            f"Audit summary model_name mismatch: expected {dense_model}, got {audit_model}"
        )

    audit_count = summary.get("chunk_count")
    if audit_count != expected_chunks:
        raise IndexerError(
            f"Audit summary chunk_count mismatch: expected {expected_chunks}, got {audit_count}"
        )

    exact = summary.get("exact_measurement")
    if exact is not True:
        raise IndexerError("Audit summary exact_measurement must be True")

    risk_counts = summary.get("risk_counts") or {}
    over_limit = risk_counts.get("strictly_over_model_limit_count", 0)
    near_limit = risk_counts.get("near_or_above_count", 0)

    if over_limit > 0:
        raise IndexerError(
            f"E5 audit failed: {over_limit} chunks strictly exceed model limit"
        )
    if near_limit > 0:
        raise IndexerError(
            f"E5 audit failed: {near_limit} chunks near model limit"
        )

    return summary


def e5_document_text(content: str, model_name: str) -> str:
    """Format document text for E5 embedding model without double-prefixing."""
    if "e5" in model_name.casefold():
        if content.startswith("passage: "):
            return content
        return f"passage: {content}"
    return content


def build_chunk_payload(
    chunk: dict[str, Any],
    corpus_sha256: str,
    chunk_count: int,
    dense_model: str,
    sparse_model: str,
) -> dict[str, Any]:
    """Build Qdrant point payload including chunk metadata and audit metadata."""
    payload = dict(chunk)
    payload["_index_corpus_sha256"] = corpus_sha256
    payload["_index_corpus_chunk_count"] = chunk_count
    payload["_index_dense_model"] = dense_model
    payload["_index_sparse_model"] = sparse_model
    payload["_indexer_version"] = INDEXER_VERSION
    return payload


def batched(items: list[Any], size: int) -> Iterable[list[Any]]:
    """Yield successive n-sized chunks from items."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _dense_vector(embedding: Any) -> list[float]:
    """Convert embedding to a list of floats."""
    if hasattr(embedding, "tolist"):
        return embedding.tolist()
    return list(embedding)


def _sparse_vector(models: Any, embedding: Any) -> Any:
    """Convert FastEmbed sparse embedding to Qdrant SparseVector struct."""
    indices = (
        embedding.indices.tolist()
        if hasattr(embedding.indices, "tolist")
        else list(embedding.indices)
    )
    values = (
        embedding.values.tolist()
        if hasattr(embedding.values, "tolist")
        else list(embedding.values)
    )
    return models.SparseVector(indices=indices, values=values)


def verify_collection_schema(
    client: Any,
    collection_name: str,
    dense_vector_name: str,
    dense_size: int,
    sparse_vector_name: str,
) -> None:
    """Verify that an existing Qdrant collection matches dense/sparse vector specs."""
    try:
        collection_info = client.get_collection(collection_name=collection_name)
    except Exception as exc:
        raise IndexerError(
            f"Failed to inspect Qdrant collection '{collection_name}': {exc}"
        ) from exc

    config = collection_info.config.params
    vectors_config = config.vectors
    sparse_config = config.sparse_vectors

    if isinstance(vectors_config, dict):
        if dense_vector_name not in vectors_config:
            raise IndexerError(
                f"Collection '{collection_name}' missing dense vector '{dense_vector_name}'"
            )
        dense_params = vectors_config[dense_vector_name]
        actual_size = getattr(dense_params, "size", None)
        if actual_size != dense_size:
            raise IndexerError(
                f"Collection dense vector '{dense_vector_name}' size mismatch: "
                f"expected {dense_size}, got {actual_size}"
            )
    else:
        raise IndexerError(
            f"Collection '{collection_name}' has invalid vectors_config schema"
        )

    if isinstance(sparse_config, dict):
        if sparse_vector_name not in sparse_config:
            raise IndexerError(
                f"Collection '{collection_name}' missing sparse vector '{sparse_vector_name}'"
            )
    else:
        raise IndexerError(
            f"Collection '{collection_name}' has invalid sparse_vectors schema"
        )


def verify_existing_collection_for_resume(
    client: Any,
    collection_name: str,
    expected_sha256: str,
    expected_chunks: int,
    dense_vector_name: str,
    dense_size: int,
    sparse_vector_name: str,
) -> None:
    """Verify that an existing collection is safe to resume."""
    verify_collection_schema(
        client, collection_name, dense_vector_name, dense_size, sparse_vector_name
    )

    point_count = client.count(collection_name=collection_name, exact=True).count
    if point_count > expected_chunks:
        raise IndexerError(
            f"Collection '{collection_name}' point count ({point_count}) "
            f"exceeds expected chunks ({expected_chunks})"
        )

    if point_count > 0:
        scroll_res = client.scroll(
            collection_name=collection_name,
            limit=min(10, point_count),
            with_payload=True,
            with_vectors=False,
        )[0]
        for pt in scroll_res:
            payload = pt.payload or {}
            fp = payload.get("_index_corpus_sha256") or payload.get("source_sha256")
            if not fp or fp.lower() != expected_sha256.lower():
                raise IndexerError(
                    f"Existing point {pt.id} in collection '{collection_name}' "
                    f"has mismatched or missing corpus SHA-256 fingerprint: {fp}"
                )


def post_index_verification(
    client: Any,
    collection_name: str,
    expected_chunks: int,
    expected_sha256: str,
    chunks: list[dict[str, Any]],
) -> None:
    """Verify collection state post-indexing."""
    point_count = client.count(collection_name=collection_name, exact=True).count
    if point_count != expected_chunks:
        raise IndexerError(
            f"Post-indexing count mismatch: expected {expected_chunks}, got {point_count}"
        )

    check_indices = [0, len(chunks) // 2, len(chunks) - 1]
    for idx in check_indices:
        target_chunk_id = chunks[idx]["chunk_id"]
        points = client.retrieve(
            collection_name=collection_name,
            ids=[target_chunk_id],
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            raise IndexerError(
                f"Post-index verification failed: point {target_chunk_id} not found"
            )
        pt = points[0]
        payload = pt.payload or {}
        if not payload.get("content"):
            raise IndexerError(
                f"Point {target_chunk_id} payload content is empty"
            )
        fp = payload.get("_index_corpus_sha256")
        if not fp or fp.lower() != expected_sha256.lower():
            raise IndexerError(
                f"Point {target_chunk_id} payload fingerprint mismatch: "
                f"expected {expected_sha256}, got {fp}"
            )


def write_summary_report(
    report_path: Path,
    summary_data: dict[str, Any],
) -> None:
    """Write summary report atomically to disk."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = report_path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(summary_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(report_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Production-safe Qdrant Indexer for Vietnamese Labor Law Corpus."
    )
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--audit-summary", type=Path, default=DEFAULT_AUDIT_SUMMARY)
    parser.add_argument("--qdrant-url", default=settings.qdrant_url)
    parser.add_argument("--collection", default=settings.qdrant_collection)
    parser.add_argument("--dense-model", default=settings.dense_embedding_model)
    parser.add_argument("--dense-vector-name", default=settings.dense_vector_name)
    parser.add_argument("--dense-size", type=int, default=settings.dense_vector_size)
    parser.add_argument("--sparse-model", default=settings.sparse_embedding_model)
    parser.add_argument("--sparse-vector-name", default=settings.sparse_vector_name)
    parser.add_argument("--batch-size", type=int, default=settings.embedding_batch_size)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--expected-chunks", type=int, default=DEFAULT_EXPECTED_CHUNKS)
    parser.add_argument("--expected-sha256", default=DEFAULT_EXPECTED_SHA256)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=DEFAULT_SUMMARY_OUTPUT,
    )
    return parser


def run_indexer(args: argparse.Namespace) -> dict[str, Any]:
    """Execute preflight checks, vector generation, Qdrant indexing, and verification."""
    if args.collection == PROBE_COLLECTION:
        raise IndexerError(
            f"The production indexer refuses to modify or write to probe collection '{PROBE_COLLECTION}'"
        )

    # Preflight Check 1 & 2: Load and validate corpus JSONL and SHA-256
    chunks, actual_sha256 = load_and_validate_chunks(
        path=args.chunks,
        expected_chunks=args.expected_chunks,
        expected_sha256=args.expected_sha256,
    )

    # Preflight Check 3 & 4: Validate strict E5 audit summary
    validate_audit_summary(
        audit_path=args.audit_summary,
        expected_sha256=actual_sha256,
        dense_model=args.dense_model,
        expected_chunks=len(chunks),
    )

    config_info = {
        "chunks": str(args.chunks),
        "chunk_count": len(chunks),
        "corpus_sha256": actual_sha256,
        "audit_summary": str(args.audit_summary),
        "qdrant_url": args.qdrant_url,
        "collection": args.collection,
        "dense_model": args.dense_model,
        "dense_vector_name": args.dense_vector_name,
        "dense_size": args.dense_size,
        "sparse_model": args.sparse_model,
        "sparse_vector_name": args.sparse_vector_name,
        "batch_size": args.batch_size,
        "threads": args.threads,
        "dry_run": args.dry_run,
        "resume": args.resume,
        "verify_only": args.verify_only,
    }

    if args.dry_run:
        LOGGER.info("Dry-run validation successful. No models loaded, no Qdrant written.")
        return {"status": "dry_run_success", "config": config_info}

    # Verify imports for actual execution
    try:
        from fastembed import SparseTextEmbedding, TextEmbedding
        from qdrant_client import QdrantClient, models
    except ImportError as exc:
        raise RuntimeError(
            "Missing retrieval dependency. Activate backend/.venv and install backend/requirements.txt."
        ) from exc

    client = QdrantClient(url=args.qdrant_url)

    if args.verify_only:
        LOGGER.info("Running verify-only checks on collection '%s'", args.collection)
        verify_collection_schema(
            client,
            args.collection,
            args.dense_vector_name,
            args.dense_size,
            args.sparse_vector_name,
        )
        post_index_verification(
            client,
            args.collection,
            len(chunks),
            actual_sha256,
            chunks,
        )
        verify_report = {
            "status": "verified",
            "timestamp_utc": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "corpus_path": str(args.chunks),
            "corpus_sha256": actual_sha256,
            "chunk_count": len(chunks),
            "collection": args.collection,
            "qdrant_url": args.qdrant_url,
            "dense_model": args.dense_model,
            "dense_vector_name": args.dense_vector_name,
            "sparse_model": args.sparse_model,
            "sparse_vector_name": args.sparse_vector_name,
            "dense_dimension": args.dense_size,
            "exact_point_count": len(chunks),
            "indexer_version": INDEXER_VERSION,
        }
        write_summary_report(args.summary_output, verify_report)
        LOGGER.info("Verify-only checks passed for collection '%s'", args.collection)
        return verify_report

    # Safety rule: if collection exists and not resume, fail
    exists = client.collection_exists(args.collection)
    if exists and not args.resume:
        raise IndexerError(
            f"Collection '{args.collection}' already exists. Pass --resume to continue or --verify-only to check."
        )

    if exists and args.resume:
        verify_existing_collection_for_resume(
            client,
            args.collection,
            actual_sha256,
            len(chunks),
            args.dense_vector_name,
            args.dense_size,
            args.sparse_vector_name,
        )

    # Initialize models BEFORE creating collection
    LOGGER.info("Loading dense model %s ...", args.dense_model)
    dense_model = TextEmbedding(
        model_name=args.dense_model,
        threads=args.threads,
    )
    LOGGER.info("Loading sparse model %s ...", args.sparse_model)
    sparse_model = SparseTextEmbedding(
        model_name=args.sparse_model,
    )

    # Recheck collection existence right before creation to prevent race condition
    if not client.collection_exists(args.collection):
        LOGGER.info("Creating collection '%s' ...", args.collection)
        client.create_collection(
            collection_name=args.collection,
            vectors_config={
                args.dense_vector_name: models.VectorParams(
                    size=args.dense_size,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                args.sparse_vector_name: models.SparseVectorParams(
                    modifier=models.Modifier.IDF,
                )
            },
        )

    # Batch Indexing
    start_time = time.perf_counter()
    indexed_count = 0

    for chunk_batch in batched(chunks, args.batch_size):
        raw_texts = [c["content"] for c in chunk_batch]
        dense_texts = [e5_document_text(t, args.dense_model) for t in raw_texts]

        dense_vectors = list(dense_model.embed(dense_texts, batch_size=args.batch_size))
        sparse_vectors = list(sparse_model.embed(raw_texts, batch_size=args.batch_size))

        if len(dense_vectors) != len(chunk_batch):
            raise IndexerError("Dense model returned unexpected batch count")
        if len(sparse_vectors) != len(chunk_batch):
            raise IndexerError("Sparse model returned unexpected batch count")

        points = []
        for chunk, dense_vec, sparse_vec in zip(
            chunk_batch, dense_vectors, sparse_vectors, strict=True
        ):
            dense_vals = _dense_vector(dense_vec)
            if len(dense_vals) != args.dense_size:
                raise IndexerError(
                    f"Dense vector size mismatch for chunk {chunk['chunk_id']}: "
                    f"expected {args.dense_size}, got {len(dense_vals)}"
                )

            points.append(
                models.PointStruct(
                    id=chunk["chunk_id"],
                    vector={
                        args.dense_vector_name: dense_vals,
                        args.sparse_vector_name: _sparse_vector(models, sparse_vec),
                    },
                    payload=build_chunk_payload(
                        chunk,
                        actual_sha256,
                        len(chunks),
                        args.dense_model,
                        args.sparse_model,
                    ),
                )
            )

        client.upsert(
            collection_name=args.collection,
            points=points,
            wait=True,
        )
        indexed_count += len(points)
        LOGGER.info("Indexed %d/%d chunks", indexed_count, len(chunks))

    elapsed_seconds = time.perf_counter() - start_time

    # Post-indexing verification
    post_index_verification(
        client,
        args.collection,
        len(chunks),
        actual_sha256,
        chunks,
    )

    summary_report = {
        "status": "completed",
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "corpus_path": str(args.chunks),
        "corpus_sha256": actual_sha256,
        "chunk_count": len(chunks),
        "collection": args.collection,
        "qdrant_url": args.qdrant_url,
        "dense_model": args.dense_model,
        "dense_vector_name": args.dense_vector_name,
        "sparse_model": args.sparse_model,
        "sparse_vector_name": args.sparse_vector_name,
        "dense_dimension": args.dense_size,
        "batch_size": args.batch_size,
        "threads": args.threads,
        "exact_point_count": len(chunks),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "collection_status": "green",
        "indexer_version": INDEXER_VERSION,
    }

    write_summary_report(args.summary_output, summary_report)
    LOGGER.info("Indexing completed successfully. Summary saved to %s", args.summary_output)
    return summary_report


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()
    try:
        run_indexer(args)
    except Exception as exc:
        LOGGER.error("Indexer failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
