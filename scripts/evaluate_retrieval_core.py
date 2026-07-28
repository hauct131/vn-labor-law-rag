"""Evaluate production retrieval core without modifying Qdrant.

This command reads dense vectors through the ``labor_law_active`` alias, builds
the selected local BM25–VnCoreNLP index, fuses rankings with RRF, and reports
article-level Hit@k, MRR@k, and latency for the smoke questions.

Run from the repository root, for example::

    PYTHONPATH=. backend/.venv/bin/python \
      -m scripts.evaluate_retrieval_core --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from backend.app.retrieval.dense_component import DenseRetriever
from backend.app.retrieval.hybrid_retriever import HybridRetriever
from backend.app.retrieval.models import LegalRetriever, RetrievalHit
from backend.app.retrieval.sparse_retriever import (
    VnCoreNlpBm25Retriever,
    load_legal_chunks,
)
from backend.app.retrieval.vncorenlp_bm25 import load_segmenter


LOGGER = logging.getLogger("retrieval_core_evaluation")

DEFAULT_CHUNKS = Path(
    "data/releases/labor-law-2026-07-28-candidate/chunks.jsonl"
)
DEFAULT_QUESTIONS = Path(
    "data/evaluation/retrieval_smoke_questions.json"
)
DEFAULT_OUTPUT = Path("experiments/retrieval_core_smoke.json")
DEFAULT_VNCORENLP_MODEL_DIR = Path("models/vncorenlp")
DEFAULT_EXPECTED_CHUNKS = 833
DEFAULT_EXPECTED_SHA256 = (
    "fd35bb1a94a3036f7977781de17bb1b49"
    "b12c58be61fc74efac68dcf8a7a8c54"
)


class EvaluationInputError(ValueError):
    """Raised when an evaluation input is incomplete or inconsistent."""


@dataclass(frozen=True)
class RetrievalMetric:
    hit: bool
    reciprocal_rank: float
    latency_ms: float
    hits: list[RetrievalHit]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate BM25–VnCoreNLP, dense E5, and Hybrid RRF on the "
            "production legal corpus without writing to Qdrant."
        )
    )
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=("sparse", "dense", "hybrid"),
        default=("sparse", "dense", "hybrid"),
    )
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--collection", default="labor_law_active")
    parser.add_argument(
        "--dense-model",
        default="intfloat/multilingual-e5-large",
    )
    parser.add_argument("--dense-vector-name", default="dense")
    parser.add_argument("--dense-size", type=int, default=1024)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument(
        "--fastembed-cache-dir",
        type=Path,
        default=(
            Path(os.environ["FASTEMBED_CACHE_DIR"])
            if os.environ.get("FASTEMBED_CACHE_DIR")
            else None
        ),
    )
    parser.add_argument(
        "--vncorenlp-model-dir",
        type=Path,
        default=DEFAULT_VNCORENLP_MODEL_DIR,
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument(
        "--expected-chunks",
        type=int,
        default=DEFAULT_EXPECTED_CHUNKS,
    )
    parser.add_argument(
        "--expected-sha256",
        default=DEFAULT_EXPECTED_SHA256,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.top_k <= 0:
        raise EvaluationInputError("top-k must be greater than zero")
    if args.candidate_k <= 0:
        raise EvaluationInputError(
            "candidate-k must be greater than zero"
        )
    if args.rrf_k < 0:
        raise EvaluationInputError("rrf-k must be zero or greater")
    if args.dense_size <= 0:
        raise EvaluationInputError(
            "dense-size must be greater than zero"
        )
    if args.threads <= 0:
        raise EvaluationInputError("threads must be greater than zero")
    if args.expected_chunks <= 0:
        raise EvaluationInputError(
            "expected-chunks must be greater than zero"
        )


def load_questions(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise EvaluationInputError(f"question file not found: {path}")
    try:
        questions = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvaluationInputError(
            f"invalid question JSON: {exc}"
        ) from exc
    if not isinstance(questions, list) or not questions:
        raise EvaluationInputError(
            "question file must contain a non-empty list"
        )

    seen_ids: set[str] = set()
    for item in questions:
        if not isinstance(item, dict):
            raise EvaluationInputError("every question must be an object")
        question_id = item.get("id")
        question = item.get("question")
        expected = item.get("expected_article_codes")
        if not isinstance(question_id, str) or not question_id:
            raise EvaluationInputError("every question needs a non-empty id")
        if question_id in seen_ids:
            raise EvaluationInputError(
                f"duplicate question id: {question_id}"
            )
        if not isinstance(question, str) or not question.strip():
            raise EvaluationInputError(
                f"question {question_id} has empty text"
            )
        if (
            not isinstance(expected, list)
            or not expected
            or not all(isinstance(code, str) and code for code in expected)
        ):
            raise EvaluationInputError(
                f"question {question_id} needs expected_article_codes"
            )
        seen_ids.add(question_id)
    return questions


def validate_ground_truth(
    chunks: Sequence[dict[str, Any]],
    questions: Sequence[dict[str, Any]],
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
        raise EvaluationInputError(
            "ground-truth article codes missing from corpus: "
            + ", ".join(missing)
        )


def validate_vncorenlp_directory(model_dir: Path) -> None:
    resolved = model_dir.expanduser()
    if not (resolved / "VnCoreNLP-1.2.jar").is_file():
        raise EvaluationInputError(
            f"VnCoreNLP jar not found in: {resolved}"
        )
    if not (resolved / "models").is_dir():
        raise EvaluationInputError(
            f"VnCoreNLP models directory not found in: {resolved}"
        )


def evaluate_one(
    retriever: LegalRetriever,
    question: str,
    expected_article_codes: set[str],
    top_k: int,
) -> RetrievalMetric:
    started = time.perf_counter()
    hits = retriever.retrieve(question, top_k=top_k)
    latency_ms = (time.perf_counter() - started) * 1000
    first_relevant_rank: int | None = None
    for rank, hit in enumerate(hits, 1):
        if hit.payload.get("article_code") in expected_article_codes:
            first_relevant_rank = rank
            break
    return RetrievalMetric(
        hit=first_relevant_rank is not None,
        reciprocal_rank=(
            1.0 / first_relevant_rank if first_relevant_rank else 0.0
        ),
        latency_ms=latency_ms,
        hits=hits,
    )


def summarize(metrics: Sequence[RetrievalMetric]) -> dict[str, float]:
    return {
        "hit_rate_at_k": round(
            sum(metric.hit for metric in metrics) / len(metrics),
            6,
        ),
        "mrr_at_k": round(statistics.fmean(
            metric.reciprocal_rank for metric in metrics
        ), 6),
        "mean_latency_ms": round(statistics.fmean(
            metric.latency_ms for metric in metrics
        ), 3),
        "median_latency_ms": round(statistics.median(
            metric.latency_ms for metric in metrics
        ), 3),
    }


def serialized_hit(hit: RetrievalHit) -> dict[str, Any]:
    return {
        "rank": hit.rank,
        "score": round(hit.score, 8),
        "chunk_id": hit.chunk_id,
        "article_code": hit.payload.get("article_code"),
        "article_title": hit.payload.get("article_title"),
        "clause_number": hit.payload.get("clause_number"),
        "retrieval_origin": hit.retrieval_origin,
        "component_ranks": dict(hit.component_ranks),
        "component_scores": {
            key: round(value, 8)
            for key, value in hit.component_scores.items()
        },
        "content_preview": " ".join(hit.content.split())[:400],
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    methods = list(dict.fromkeys(args.methods))
    questions = load_questions(args.questions)
    chunks, corpus_sha256 = load_legal_chunks(
        args.chunks,
        expected_chunks=args.expected_chunks,
        expected_sha256=args.expected_sha256,
    )
    validate_ground_truth(chunks, questions)

    needs_sparse = bool({"sparse", "hybrid"}.intersection(methods))
    needs_dense = bool({"dense", "hybrid"}.intersection(methods))
    if needs_sparse:
        validate_vncorenlp_directory(args.vncorenlp_model_dir)

    config = {
        "chunks": str(args.chunks),
        "chunk_count": len(chunks),
        "corpus_sha256": corpus_sha256,
        "questions": str(args.questions),
        "question_count": len(questions),
        "methods": methods,
        "top_k": args.top_k,
        "candidate_k": args.candidate_k,
        "rrf_k": args.rrf_k,
        "qdrant_url": args.qdrant_url,
        "collection": args.collection,
        "dense_model": args.dense_model,
        "dense_vector_name": args.dense_vector_name,
        "dense_size": args.dense_size,
        "fastembed_cache_dir": (
            str(args.fastembed_cache_dir)
            if args.fastembed_cache_dir
            else None
        ),
        "vncorenlp_model_dir": str(args.vncorenlp_model_dir),
        "writes_qdrant": False,
    }
    if args.dry_run:
        return {"status": "dry_run_pass", "config": config}

    sparse: VnCoreNlpBm25Retriever | None = None
    dense: DenseRetriever | None = None
    retrievers: dict[str, LegalRetriever] = {}

    if needs_sparse:
        LOGGER.info("Loading VnCoreNLP and building local BM25 index")
        sparse = VnCoreNlpBm25Retriever.from_chunks(
            chunks,
            segmenter=load_segmenter(args.vncorenlp_model_dir),
            default_top_k=args.top_k,
            corpus_sha256=corpus_sha256,
        )
        config["sparse_metadata"] = sparse.metadata
        if "sparse" in methods:
            retrievers["sparse"] = sparse

    if needs_dense:
        dense = DenseRetriever(
            qdrant_url=args.qdrant_url,
            collection_name=args.collection,
            model_name=args.dense_model,
            vector_name=args.dense_vector_name,
            vector_size=args.dense_size,
            default_top_k=args.top_k,
            threads=args.threads,
            cache_dir=args.fastembed_cache_dir,
            expected_corpus_sha256=corpus_sha256,
        )
        LOGGER.info("Loading and warming up dense E5 retriever")
        dense.warmup()
        if "dense" in methods:
            retrievers["dense"] = dense

    if "hybrid" in methods:
        if sparse is None or dense is None:
            raise AssertionError("hybrid dependencies were not initialized")
        retrievers["hybrid"] = HybridRetriever(
            sparse_retriever=sparse,
            dense_retriever=dense,
            default_top_k=args.top_k,
            candidate_k=args.candidate_k,
            rrf_k=args.rrf_k,
        )

    method_metrics: dict[str, list[RetrievalMetric]] = {
        method: [] for method in methods
    }
    question_results: list[dict[str, Any]] = []
    for question in questions:
        expected = set(question["expected_article_codes"])
        item = {
            "id": question["id"],
            "category": question.get("category"),
            "question": question["question"],
            "expected_article_codes": question["expected_article_codes"],
            "methods": {},
        }
        for method in methods:
            metric = evaluate_one(
                retrievers[method],
                question["question"],
                expected,
                args.top_k,
            )
            method_metrics[method].append(metric)
            item["methods"][method] = {
                "hit": metric.hit,
                "reciprocal_rank": round(metric.reciprocal_rank, 8),
                "latency_ms": round(metric.latency_ms, 3),
                "results": [serialized_hit(hit) for hit in metric.hits],
            }
        question_results.append(item)

    report = {
        "status": "completed",
        "config": config,
        "summary": {
            method: summarize(method_metrics[method])
            for method in methods
        },
        "questions": question_results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = args.output.with_suffix(args.output.suffix + ".tmp")
    temp_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_output.replace(args.output)
    LOGGER.info("Evaluation report saved to %s", args.output)
    return report


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        report = run(args)
    except Exception as exc:
        LOGGER.error("Retrieval evaluation failed: %s", exc)
        sys.exit(1)
    print(json.dumps(
        {
            "status": report["status"],
            "summary": report.get("summary"),
            "config": report["config"],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
