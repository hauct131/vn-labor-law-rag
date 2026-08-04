"""Benchmark dense E5 and sparse BM25 retrieval on a disposable Qdrant collection.

This is a Day-4 model probe, not the production ingestion command. It creates
or reuses a non-production collection, indexes the current legal chunks with
two named vectors, and reports article-level Hit@k, MRR@k, and latency.

Run from the repository root:

    python -m scripts.test_sparse_dense --dry-run
    python -m scripts.test_sparse_dense --recreate
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from typing import Any, Iterable

from backend.app.retrieval.vncorenlp_bm25 import (
    MODEL_NAME as VNCORENLP_BM25_MODEL,
    VnCoreNlpBm25,
)


LOGGER = logging.getLogger("retrieval_model_probe")

DEFAULT_CHUNKS = Path(
    "data/releases/labor-law-2026-07-28-candidate/chunks.jsonl"
)
DEFAULT_QUESTIONS = Path(
    "data/evaluation/retrieval_smoke_questions.json"
)
DEFAULT_OUTPUT = Path("experiments/sparse_dense_model_probe.json")
DEFAULT_COLLECTION = "labor_law_model_probe"
PRODUCTION_COLLECTION = "labor_law"
DEFAULT_DENSE_MAX_TOKENS = 512
DEFAULT_VNCORENLP_MODEL_DIR = Path("models/vncorenlp")


class ProbeInputError(ValueError):
    """Raised when benchmark input is invalid or unsafe."""


@dataclass(frozen=True)
class QueryMetric:
    """One retrieval method's result for one benchmark question."""

    hit: bool
    reciprocal_rank: float
    latency_ms: float
    results: list[dict[str, Any]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare multilingual E5 dense retrieval with Qdrant BM25 on "
            "the validated legal chunk corpus."
        )
    )
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument(
        "--dense-model",
        default="intfloat/multilingual-e5-large",
    )
    parser.add_argument("--dense-size", type=int, default=1024)
    parser.add_argument(
        "--dense-max-tokens",
        type=int,
        default=DEFAULT_DENSE_MAX_TOKENS,
        help=(
            "Published input-token limit of the dense model; used only for "
            "the truncation-risk proxy in the report."
        ),
    )
    parser.add_argument("--dense-vector-name", default="dense")
    parser.add_argument("--sparse-model", default="Qdrant/bm25")
    parser.add_argument("--sparse-vector-name", default="sparse")
    parser.add_argument(
        "--vncorenlp-model-dir",
        type=Path,
        default=DEFAULT_VNCORENLP_MODEL_DIR,
        help=(
            "Local folder containing VnCoreNLP-1.2.jar and models/. Used "
            f"only when --sparse-model={VNCORENLP_BM25_MODEL}."
        ),
    )
    parser.add_argument(
        "--sparse-disable-stemmer",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Disable FastEmbed's English BM25 stemmer/stopwords. Keep this "
            "enabled for Vietnamese text."
        ),
    )
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--expected-chunks", type=int, default=833)
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete and recreate only the disposable probe collection.",
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="Reuse vectors already present in the probe collection.",
    )
    parser.add_argument(
        "--sparse-only-reindex",
        action="store_true",
        help=(
            "Replace only sparse named vectors in an existing probe "
            "collection, preserving the expensive dense vectors."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate files/configuration without loading models or Qdrant.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser


def load_chunks(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ProbeInputError(f"Chunk file not found: {path}")

    chunks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, 1):
            if not raw_line.strip():
                continue
            try:
                chunk = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ProbeInputError(
                    f"Invalid JSON at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(chunk, dict):
                raise ProbeInputError(
                    f"Chunk at {path}:{line_number} must be an object"
                )
            chunk_id = chunk.get("chunk_id")
            content = chunk.get("content")
            if not isinstance(chunk_id, str) or not chunk_id:
                raise ProbeInputError(
                    f"Chunk at {path}:{line_number} has no chunk_id"
                )
            if chunk_id in seen_ids:
                raise ProbeInputError(f"Duplicate chunk_id: {chunk_id}")
            if not isinstance(content, str) or not content.strip():
                raise ProbeInputError(f"Chunk {chunk_id} has empty content")
            seen_ids.add(chunk_id)
            chunks.append(chunk)
    if not chunks:
        raise ProbeInputError(f"No chunks found in: {path}")
    return chunks


def load_questions(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ProbeInputError(f"Question file not found: {path}")
    try:
        questions = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProbeInputError(f"Invalid question JSON: {exc}") from exc
    if not isinstance(questions, list) or not questions:
        raise ProbeInputError("Question file must contain a non-empty list")

    seen_ids: set[str] = set()
    for item in questions:
        if not isinstance(item, dict):
            raise ProbeInputError("Every question must be an object")
        question_id = item.get("id")
        question = item.get("question")
        expected_codes = item.get("expected_article_codes")
        if not isinstance(question_id, str) or not question_id:
            raise ProbeInputError("Every question must have a non-empty id")
        if question_id in seen_ids:
            raise ProbeInputError(f"Duplicate question id: {question_id}")
        if not isinstance(question, str) or not question.strip():
            raise ProbeInputError(f"Question {question_id} has empty text")
        if (
            not isinstance(expected_codes, list)
            or not expected_codes
            or not all(isinstance(code, str) and code for code in expected_codes)
        ):
            raise ProbeInputError(
                f"Question {question_id} needs expected_article_codes"
            )
        seen_ids.add(question_id)
    return questions


def validate_ground_truth(
    chunks: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> None:
    available_codes = {
        chunk.get("article_code")
        for chunk in chunks
        if isinstance(chunk.get("article_code"), str)
    }
    missing = sorted({
        code
        for question in questions
        for code in question["expected_article_codes"]
        if code not in available_codes
    })
    if missing:
        raise ProbeInputError(
            "Ground-truth article codes missing from chunks: "
            + ", ".join(missing)
        )


def validate_args(args: argparse.Namespace) -> None:
    if args.collection == PRODUCTION_COLLECTION:
        raise ProbeInputError(
            "The model probe refuses to use the production collection "
            f"'{PRODUCTION_COLLECTION}'"
        )
    if args.dense_size <= 0:
        raise ProbeInputError("dense-size must be greater than zero")
    if args.dense_max_tokens <= 0:
        raise ProbeInputError("dense-max-tokens must be greater than zero")
    if args.batch_size <= 0:
        raise ProbeInputError("batch-size must be greater than zero")
    if args.threads <= 0:
        raise ProbeInputError("threads must be greater than zero")
    if args.top_k <= 0:
        raise ProbeInputError("top-k must be greater than zero")
    if args.expected_chunks <= 0:
        raise ProbeInputError("expected-chunks must be greater than zero")
    if args.recreate and args.skip_index:
        raise ProbeInputError("--recreate and --skip-index cannot be combined")
    if args.sparse_only_reindex and (args.recreate or args.skip_index):
        raise ProbeInputError(
            "--sparse-only-reindex cannot be combined with --recreate or "
            "--skip-index"
        )


def e5_document_text(content: str, model_name: str) -> str:
    """Apply the passage prefix required by E5 retrieval models."""
    if "e5" in model_name.casefold():
        return f"passage: {content}"
    return content


def e5_query_text(question: str, model_name: str) -> str:
    """Apply the query prefix required by E5 retrieval models."""
    if "e5" in model_name.casefold():
        return f"query: {question}"
    return question


def chunk_payload(chunk: dict[str, Any]) -> dict[str, Any]:
    """Build a compact, inspectable payload for the disposable probe."""
    keys = (
        "chunk_id",
        "chunk_key",
        "chunk_type",
        "container_type",
        "parent_article_id",
        "parent_attachment_id",
        "article_code",
        "article_title",
        "clause_number",
        "point_labels",
        "source_type",
        "source_document_id",
        "token_count",
        "tokenizer_name",
    )
    payload = {key: chunk.get(key) for key in keys}
    payload["content"] = chunk["content"]
    return payload


def truncation_risk_proxy(
    chunks: list[dict[str, Any]],
    model_max_tokens: int,
) -> dict[str, Any]:
    """Estimate dense truncation risk from the chunker's token metadata.

    The chunker currently records cl100k_base counts, not E5 tokenizer counts,
    so this is deliberately a warning proxy rather than an exact measurement.
    The 32-token near-limit band covers the E5 prefix and tokenizer mismatch.
    """
    token_counts = [
        chunk["token_count"]
        for chunk in chunks
        if isinstance(chunk.get("token_count"), int)
    ]
    near_limit = max(1, model_max_tokens - 32)
    return {
        "is_exact": False,
        "model_max_tokens": model_max_tokens,
        "near_limit_threshold": near_limit,
        "measured_chunk_count": len(token_counts),
        "missing_token_count": len(chunks) - len(token_counts),
        "near_or_above_count": sum(
            count >= near_limit for count in token_counts
        ),
        "at_or_above_model_limit_count": sum(
            count >= model_max_tokens for count in token_counts
        ),
        "source_tokenizers": sorted({
            str(chunk["tokenizer_name"])
            for chunk in chunks
            if chunk.get("tokenizer_name")
        }),
        "warning": (
            "Proxy only: chunk token_count uses the chunker's tokenizer, not "
            "the dense model tokenizer. Inspect long-chunk misses manually."
        ),
    }


def batched(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _sparse_vector(models: Any, embedding: Any) -> Any:
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


def _dense_vector(embedding: Any) -> list[float]:
    if hasattr(embedding, "tolist"):
        return embedding.tolist()
    return list(embedding)


def create_or_validate_collection(client: Any, models: Any, args: argparse.Namespace) -> None:
    exists = client.collection_exists(args.collection)
    if args.sparse_only_reindex and not exists:
        raise RuntimeError(
            "--sparse-only-reindex requires an existing probe collection"
        )
    if exists and args.recreate:
        LOGGER.warning("Deleting disposable probe collection %s", args.collection)
        client.delete_collection(args.collection)
        exists = False

    if not exists:
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


def index_chunks(
    client: Any,
    models: Any,
    dense_model: Any,
    sparse_model: Any,
    chunks: list[dict[str, Any]],
    args: argparse.Namespace,
) -> float:
    started = time.perf_counter()
    indexed = 0
    for chunk_batch in batched(chunks, args.batch_size):
        raw_texts = [chunk["content"] for chunk in chunk_batch]
        dense_texts = [
            e5_document_text(text, args.dense_model) for text in raw_texts
        ]
        dense_vectors = list(
            dense_model.embed(dense_texts, batch_size=args.batch_size)
        )
        sparse_vectors = list(
            sparse_model.embed(raw_texts, batch_size=args.batch_size)
        )
        if len(dense_vectors) != len(chunk_batch):
            raise RuntimeError("Dense model returned an unexpected batch size")
        if len(sparse_vectors) != len(chunk_batch):
            raise RuntimeError("Sparse model returned an unexpected batch size")

        points = []
        for chunk, dense_vector, sparse_vector in zip(
            chunk_batch,
            dense_vectors,
            sparse_vectors,
            strict=True,
        ):
            dense_values = _dense_vector(dense_vector)
            if len(dense_values) != args.dense_size:
                raise RuntimeError(
                    f"Dense dimension mismatch for {chunk['chunk_id']}: "
                    f"expected {args.dense_size}, got {len(dense_values)}"
                )
            points.append(models.PointStruct(
                id=chunk["chunk_id"],
                vector={
                    args.dense_vector_name: dense_values,
                    args.sparse_vector_name: _sparse_vector(
                        models, sparse_vector
                    ),
                },
                payload=chunk_payload(chunk),
            ))
        client.upsert(
            collection_name=args.collection,
            points=points,
            wait=True,
        )
        indexed += len(points)
        LOGGER.info("Indexed %d/%d chunks", indexed, len(chunks))

    point_count = client.count(
        collection_name=args.collection,
        exact=True,
    ).count
    if point_count != len(chunks):
        raise RuntimeError(
            f"Probe point count mismatch: expected {len(chunks)}, got {point_count}"
        )
    return time.perf_counter() - started


def index_sparse_vectors(
    client: Any,
    models: Any,
    sparse_model: Any,
    chunks: list[dict[str, Any]],
    args: argparse.Namespace,
) -> float:
    """Replace only BM25 named vectors while preserving dense vectors."""
    started = time.perf_counter()
    indexed = 0
    for chunk_batch in batched(chunks, args.batch_size):
        raw_texts = [chunk["content"] for chunk in chunk_batch]
        sparse_vectors = list(
            sparse_model.embed(raw_texts, batch_size=args.batch_size)
        )
        if len(sparse_vectors) != len(chunk_batch):
            raise RuntimeError("Sparse model returned an unexpected batch size")
        points = [
            models.PointVectors(
                id=chunk["chunk_id"],
                vector={
                    args.sparse_vector_name: _sparse_vector(
                        models,
                        sparse_vector,
                    )
                },
            )
            for chunk, sparse_vector in zip(
                chunk_batch,
                sparse_vectors,
                strict=True,
            )
        ]
        client.update_vectors(
            collection_name=args.collection,
            points=points,
            wait=True,
        )
        indexed += len(points)
        LOGGER.info("Updated sparse vectors %d/%d", indexed, len(chunks))

    point_count = client.count(
        collection_name=args.collection,
        exact=True,
    ).count
    if point_count != len(chunks):
        raise RuntimeError(
            f"Probe point count mismatch: expected {len(chunks)}, "
            f"got {point_count}"
        )
    return time.perf_counter() - started


def score_points(
    points: list[Any],
    expected_codes: set[str],
    latency_ms: float,
) -> QueryMetric:
    results = []
    first_relevant_rank: int | None = None
    for rank, point in enumerate(points, 1):
        payload = point.payload or {}
        article_code = payload.get("article_code")
        if first_relevant_rank is None and article_code in expected_codes:
            first_relevant_rank = rank
        results.append({
            "rank": rank,
            "score": round(float(point.score), 8),
            "chunk_id": payload.get("chunk_id"),
            "chunk_key": payload.get("chunk_key"),
            "article_code": article_code,
            "article_title": payload.get("article_title"),
            "chunk_type": payload.get("chunk_type"),
            "token_count": payload.get("token_count"),
            "content_preview": " ".join(
                str(payload.get("content") or "").split()
            )[:400],
        })
    return QueryMetric(
        hit=first_relevant_rank is not None,
        reciprocal_rank=(
            1.0 / first_relevant_rank if first_relevant_rank else 0.0
        ),
        latency_ms=latency_ms,
        results=results,
    )


def retrieve_one(
    client: Any,
    models: Any,
    dense_model: Any,
    sparse_model: Any,
    question: str,
    expected_codes: set[str],
    args: argparse.Namespace,
) -> tuple[QueryMetric, QueryMetric]:
    dense_query = e5_query_text(question, args.dense_model)

    started = time.perf_counter()
    dense_embedding = next(iter(dense_model.embed([dense_query])))
    dense_points = client.query_points(
        collection_name=args.collection,
        query=_dense_vector(dense_embedding),
        using=args.dense_vector_name,
        limit=args.top_k,
        with_payload=True,
    ).points
    dense_latency_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    sparse_embedding = next(iter(sparse_model.query_embed([question])))
    sparse_points = client.query_points(
        collection_name=args.collection,
        query=_sparse_vector(models, sparse_embedding),
        using=args.sparse_vector_name,
        limit=args.top_k,
        with_payload=True,
    ).points
    sparse_latency_ms = (time.perf_counter() - started) * 1000

    return (
        score_points(dense_points, expected_codes, dense_latency_ms),
        score_points(sparse_points, expected_codes, sparse_latency_ms),
    )


def summarize(metrics: list[QueryMetric]) -> dict[str, float]:
    return {
        "hit_rate_at_k": round(
            sum(metric.hit for metric in metrics) / len(metrics), 6
        ),
        "mrr_at_k": round(
            statistics.fmean(metric.reciprocal_rank for metric in metrics),
            6,
        ),
        "mean_latency_ms": round(
            statistics.fmean(metric.latency_ms for metric in metrics),
            3,
        ),
        "median_latency_ms": round(
            statistics.median(metric.latency_ms for metric in metrics),
            3,
        ),
    }


def write_outputs(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    csv_path = output_path.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as target:
        fieldnames = [
            "id",
            "category",
            "question",
            "expected_article_codes",
            "dense_hit",
            "dense_reciprocal_rank",
            "dense_latency_ms",
            "dense_top_article_codes",
            "sparse_hit",
            "sparse_reciprocal_rank",
            "sparse_latency_ms",
            "sparse_top_article_codes",
        ]
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        for item in report["questions"]:
            writer.writerow({
                "id": item["id"],
                "category": item["category"],
                "question": item["question"],
                "expected_article_codes": ",".join(
                    item["expected_article_codes"]
                ),
                "dense_hit": item["dense"]["hit"],
                "dense_reciprocal_rank": item["dense"][
                    "reciprocal_rank"
                ],
                "dense_latency_ms": item["dense"]["latency_ms"],
                "dense_top_article_codes": ",".join(
                    str(result["article_code"])
                    for result in item["dense"]["results"]
                ),
                "sparse_hit": item["sparse"]["hit"],
                "sparse_reciprocal_rank": item["sparse"][
                    "reciprocal_rank"
                ],
                "sparse_latency_ms": item["sparse"]["latency_ms"],
                "sparse_top_article_codes": ",".join(
                    str(result["article_code"])
                    for result in item["sparse"]["results"]
                ),
            })


def metric_dict(metric: QueryMetric) -> dict[str, Any]:
    return {
        "hit": metric.hit,
        "reciprocal_rank": round(metric.reciprocal_rank, 8),
        "latency_ms": round(metric.latency_ms, 3),
        "results": metric.results,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    chunks = load_chunks(args.chunks)
    questions = load_questions(args.questions)
    validate_ground_truth(chunks, questions)
    if len(chunks) != args.expected_chunks:
        raise ProbeInputError(
            f"Expected {args.expected_chunks} chunks, found {len(chunks)}"
        )

    config = {
        "chunks": str(args.chunks),
        "chunk_count": len(chunks),
        "questions": str(args.questions),
        "question_count": len(questions),
        "qdrant_url": args.qdrant_url,
        "collection": args.collection,
        "dense_model": args.dense_model,
        "dense_size": args.dense_size,
        "dense_max_tokens": args.dense_max_tokens,
        "dense_vector_name": args.dense_vector_name,
        "sparse_model": args.sparse_model,
        "sparse_vector_name": args.sparse_vector_name,
        "sparse_disable_stemmer": (
            args.sparse_disable_stemmer
            if args.sparse_model == "Qdrant/bm25"
            else None
        ),
        "sparse_query_method": "query_embed",
        "top_k": args.top_k,
        "batch_size": args.batch_size,
        "threads": args.threads,
        "dense_truncation_risk_proxy": truncation_risk_proxy(
            chunks,
            args.dense_max_tokens,
        ),
    }
    if args.sparse_model == VNCORENLP_BM25_MODEL:
        config["vncorenlp_model_dir"] = str(
            args.vncorenlp_model_dir.expanduser().resolve()
        )
    if args.dry_run:
        return {"status": "dry_run_pass", "config": config}

    try:
        from fastembed import SparseTextEmbedding, TextEmbedding
        from qdrant_client import QdrantClient, models
    except ImportError as exc:
        raise RuntimeError(
            "Missing retrieval dependency. Activate backend/.venv and install "
            "backend/requirements.txt."
        ) from exc

    LOGGER.info("Loading dense model %s", args.dense_model)
    dense_model = TextEmbedding(
        model_name=args.dense_model,
        threads=args.threads,
    )
    LOGGER.info("Loading sparse model %s", args.sparse_model)
    if args.sparse_model == VNCORENLP_BM25_MODEL:
        sparse_model = VnCoreNlpBm25.from_model_dir(
            [chunk["content"] for chunk in chunks],
            args.vncorenlp_model_dir,
        )
        config["sparse_analyzer"] = sparse_model.metadata
    else:
        sparse_kwargs: dict[str, Any] = {}
        if args.sparse_model == "Qdrant/bm25":
            sparse_kwargs["disable_stemmer"] = (
                args.sparse_disable_stemmer
            )
        sparse_model = SparseTextEmbedding(
            model_name=args.sparse_model,
            **sparse_kwargs,
        )

    client = QdrantClient(url=args.qdrant_url)
    create_or_validate_collection(client, models, args)
    indexing_seconds = 0.0
    if args.sparse_only_reindex:
        indexing_seconds = index_sparse_vectors(
            client,
            models,
            sparse_model,
            chunks,
            args,
        )
    elif not args.skip_index:
        indexing_seconds = index_chunks(
            client,
            models,
            dense_model,
            sparse_model,
            chunks,
            args,
        )
    point_count = client.count(
        collection_name=args.collection,
        exact=True,
    ).count
    if point_count != len(chunks):
        raise RuntimeError(
            f"Expected {len(chunks)} probe points, found {point_count}"
        )

    dense_metrics: list[QueryMetric] = []
    sparse_metrics: list[QueryMetric] = []
    question_reports = []
    for question in questions:
        expected_codes = set(question["expected_article_codes"])
        dense_metric, sparse_metric = retrieve_one(
            client,
            models,
            dense_model,
            sparse_model,
            question["question"],
            expected_codes,
            args,
        )
        dense_metrics.append(dense_metric)
        sparse_metrics.append(sparse_metric)
        question_reports.append({
            **question,
            "dense": metric_dict(dense_metric),
            "sparse": metric_dict(sparse_metric),
        })
        LOGGER.info(
            "%s dense=%s sparse=%s",
            question["id"],
            "HIT" if dense_metric.hit else "MISS",
            "HIT" if sparse_metric.hit else "MISS",
        )

    report = {
        "status": "completed",
        "config": config,
        "point_count": point_count,
        "indexing_seconds": round(indexing_seconds, 3),
        "dense_summary": summarize(dense_metrics),
        "sparse_summary": summarize(sparse_metrics),
        "questions": question_reports,
    }
    write_outputs(report, args.output)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        report = run(args)
    except (ProbeInputError, RuntimeError, OSError) as exc:
        LOGGER.error("%s", exc)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.dry_run:
        print(f"JSON report: {args.output}")
        print(f"CSV report: {args.output.with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
