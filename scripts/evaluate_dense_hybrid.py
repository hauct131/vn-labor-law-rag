#!/usr/bin/env python3
"""Evaluate dense and hybrid retrieval on a JSONL legal corpus.

This script intentionally runs locally without Qdrant so retrieval quality can
be measured independently of indexing/network configuration.

Requirements:
    sentence-transformers
    numpy

It reuses BM25Index and metric helpers from:
    scripts/evaluate_lexical_baseline.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
from sentence_transformers import SentenceTransformer

from evaluate_lexical_baseline import (
    BM25Index,
    article_code,
    chunk_id,
    chunk_search_text,
    first_nonempty,
    load_json,
    load_jsonl,
    percentile,
    recall,
    reciprocal_rank,
    sha256_file,
)


def slug(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in value
    )


def encode_documents(
    model: SentenceTransformer,
    texts: list[str],
    *,
    batch_size: int,
) -> np.ndarray:
    values = ["passage: " + text for text in texts]
    return np.asarray(
        model.encode(
            values,
            batch_size=batch_size,
            show_progress_bar=True,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ),
        dtype=np.float32,
    )


def encode_queries(
    model: SentenceTransformer,
    texts: list[str],
    *,
    batch_size: int,
) -> np.ndarray:
    values = ["query: " + text for text in texts]
    return np.asarray(
        model.encode(
            values,
            batch_size=batch_size,
            show_progress_bar=True,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ),
        dtype=np.float32,
    )


def load_or_build_document_embeddings(
    *,
    model: SentenceTransformer,
    model_name: str,
    texts: list[str],
    corpus_sha256: str,
    cache_dir: Path,
    batch_size: int,
) -> tuple[np.ndarray, Path, bool]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / (
        f"{slug(model_name)}-{corpus_sha256[:16]}-documents.npz"
    )

    if path.exists():
        payload = np.load(path)
        embeddings = np.asarray(payload["embeddings"], dtype=np.float32)
        if embeddings.shape[0] != len(texts):
            raise ValueError(
                f"Embedding cache row count mismatch: "
                f"{embeddings.shape[0]} != {len(texts)}"
            )
        return embeddings, path, True

    embeddings = encode_documents(
        model,
        texts,
        batch_size=batch_size,
    )
    np.savez_compressed(
        path,
        embeddings=embeddings,
        model_name=np.asarray(model_name),
        corpus_sha256=np.asarray(corpus_sha256),
    )
    return embeddings, path, False


def dense_rank(
    query_embedding: np.ndarray,
    document_embeddings: np.ndarray,
    limit: int,
) -> list[tuple[int, float]]:
    scores = document_embeddings @ query_embedding
    if limit >= len(scores):
        indices = np.argsort(-scores)
    else:
        candidate = np.argpartition(-scores, limit - 1)[:limit]
        indices = candidate[np.argsort(-scores[candidate])]
    return [(int(index), float(scores[index])) for index in indices]


def reciprocal_rank_fusion(
    rankings: list[list[tuple[int, float]]],
    *,
    limit: int,
    rrf_constant: int,
) -> list[tuple[int, float]]:
    scores: dict[int, float] = defaultdict(float)
    best_rank: dict[int, int] = {}

    for ranking in rankings:
        for rank, (document_index, _score) in enumerate(ranking, start=1):
            scores[document_index] += 1.0 / (rrf_constant + rank)
            previous = best_rank.get(document_index)
            if previous is None or rank < previous:
                best_rank[document_index] = rank

    ordered = sorted(
        scores,
        key=lambda index: (
            -scores[index],
            best_rank[index],
            index,
        ),
    )
    return [
        (index, scores[index])
        for index in ordered[:limit]
    ]


def diversify_articles(
    ranking: list[tuple[int, float]],
    chunks: list[dict[str, Any]],
    *,
    limit: int,
    max_chunks_per_article: int,
    drop_missing_article_code: bool,
) -> list[tuple[int, float]]:
    if max_chunks_per_article <= 0 and not drop_missing_article_code:
        return ranking[:limit]

    counts: dict[str, int] = defaultdict(int)
    output: list[tuple[int, float]] = []

    for index, score in ranking:
        code = article_code(chunks[index])
        if not code and drop_missing_article_code:
            continue

        grouping_key = code or f"__chunk__:{chunk_id(chunks[index])}"
        if (
            max_chunks_per_article > 0
            and counts[grouping_key] >= max_chunks_per_article
        ):
            continue

        counts[grouping_key] += 1
        output.append((index, score))
        if len(output) >= limit:
            break

    return output


def evaluate_ranking(
    *,
    retriever_name: str,
    ranking_function: Callable[
        [str, np.ndarray, int],
        list[tuple[int, float]],
    ],
    questions: list[dict[str, Any]],
    query_embeddings: np.ndarray,
    chunks: list[dict[str, Any]],
    k_values: list[int],
    candidate_k: int,
    max_chunks_per_article: int,
    drop_missing_article_code: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    aggregate: dict[int, dict[str, list[float]]] = {
        k: {
            "any_article_hit": [],
            "all_article_hit": [],
            "article_recall": [],
            "article_mrr": [],
            "any_evidence_hit": [],
            "all_evidence_hit": [],
            "evidence_recall": [],
            "evidence_mrr": [],
        }
        for k in k_values
    }

    latency_values: list[float] = []
    per_question: list[dict[str, Any]] = []
    max_k = max(k_values)

    for question_index, question in enumerate(questions):
        query = first_nonempty(question, ["question", "query", "text"])

        started = time.perf_counter()
        raw_ranking = ranking_function(
            query,
            query_embeddings[question_index],
            max(candidate_k, max_k),
        )
        ranking = diversify_articles(
            raw_ranking,
            chunks,
            limit=max_k,
            max_chunks_per_article=max_chunks_per_article,
            drop_missing_article_code=drop_missing_article_code,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        latency_values.append(latency_ms)

        retrieved = []
        for position, (index, score) in enumerate(ranking, start=1):
            chunk = chunks[index]
            retrieved.append(
                {
                    "rank": position,
                    "score": round(float(score), 8),
                    "chunk_id": chunk_id(chunk),
                    "article_code": article_code(chunk),
                    "heading": first_nonempty(
                        chunk,
                        ["heading", "article_title", "title"],
                    ),
                }
            )

        expected_articles = {
            str(value)
            for value in question.get("expected_article_codes", [])
            if value
        }
        expected_evidence = {
            str(value)
            for value in question.get("evidence_chunk_ids", [])
            if value
        }

        ranked_ids = [item["chunk_id"] for item in retrieved]
        ranked_articles = [item["article_code"] for item in retrieved]
        question_metrics: dict[str, Any] = {}

        for k in k_values:
            top_ids = ranked_ids[:k]
            top_articles = ranked_articles[:k]
            top_id_set = set(top_ids)
            top_article_set = {
                value for value in top_articles if value
            }

            values = {
                "any_article_hit": float(
                    not expected_articles
                    or bool(expected_articles & top_article_set)
                ),
                "all_article_hit": float(
                    not expected_articles
                    or expected_articles <= top_article_set
                ),
                "article_recall": recall(
                    expected_articles,
                    top_article_set,
                ),
                "article_mrr": reciprocal_rank(
                    expected_articles,
                    top_articles,
                ),
                "any_evidence_hit": float(
                    not expected_evidence
                    or bool(expected_evidence & top_id_set)
                ),
                "all_evidence_hit": float(
                    not expected_evidence
                    or expected_evidence <= top_id_set
                ),
                "evidence_recall": recall(
                    expected_evidence,
                    top_id_set,
                ),
                "evidence_mrr": reciprocal_rank(
                    expected_evidence,
                    top_ids,
                ),
            }

            for name, value in values.items():
                aggregate[k][name].append(value)

            question_metrics[f"at_{k}"] = {
                name: round(value, 6)
                for name, value in values.items()
            }

        per_question.append(
            {
                "question_id": question.get("id"),
                "question": query,
                "expected_article_codes": sorted(expected_articles),
                "expected_evidence_chunk_ids": sorted(expected_evidence),
                "latency_ms": round(latency_ms, 4),
                "metrics": question_metrics,
                "retrieved": retrieved,
            }
        )

    summary = {
        f"at_{k}": {
            name: round(statistics.fmean(values), 6)
            for name, values in aggregate[k].items()
        }
        for k in k_values
    }

    latency = {
        "mean": round(statistics.fmean(latency_values), 4),
        "p50": round(percentile(latency_values, 0.50), 4),
        "p95": round(percentile(latency_values, 0.95), 4),
        "max": round(max(latency_values, default=0.0), 4),
    }

    return {
        "name": retriever_name,
        "summary": summary,
        "latency_ms_excluding_embedding": latency,
    }, per_question


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path(
            "data/evaluation/"
            "golden_questions_v1_rebased_current_phapdien.json"
        ),
    )
    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path("data/processed/legal_chunks.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/evaluation/results"),
    )
    parser.add_argument(
        "--embedding-cache-dir",
        type=Path,
        default=Path("data/evaluation/cache/embeddings"),
    )
    parser.add_argument(
        "--model",
        default="intfloat/multilingual-e5-small",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Examples: cpu, cuda. Omit for SentenceTransformer auto-detect.",
    )
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["dense", "hybrid"],
        default=["dense", "hybrid"],
    )
    parser.add_argument("--k", type=int, nargs="+", default=[5, 10, 20])
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--rrf-constant", type=int, default=60)
    parser.add_argument(
        "--max-chunks-per-article",
        type=int,
        default=0,
        help="0 disables article diversification.",
    )
    parser.add_argument(
        "--drop-missing-article-code",
        action="store_true",
    )
    parser.add_argument("--bm25-k1", type=float, default=1.5)
    parser.add_argument("--bm25-b", type=float, default=0.75)
    parser.add_argument("--include-disabled", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    k_values = sorted(set(args.k))
    if not k_values or min(k_values) <= 0:
        raise ValueError("--k must contain positive integers.")
    if args.candidate_k < max(k_values):
        raise ValueError("--candidate-k must be >= max(--k).")

    golden = load_json(args.golden)
    questions = golden.get("questions", golden)
    if not isinstance(questions, list):
        raise ValueError("Golden dataset must contain a questions list.")
    if not args.include_disabled:
        questions = [
            question
            for question in questions
            if question.get("benchmark_enabled", True)
        ]

    chunks = load_jsonl(args.chunks)
    document_texts = [chunk_search_text(chunk) for chunk in chunks]
    query_texts = [
        first_nonempty(question, ["question", "query", "text"])
        for question in questions
    ]

    model = SentenceTransformer(
        args.model,
        device=args.device,
    )
    corpus_sha = sha256_file(args.chunks)

    embedding_started = time.perf_counter()
    document_embeddings, cache_path, cache_hit = (
        load_or_build_document_embeddings(
            model=model,
            model_name=args.model,
            texts=document_texts,
            corpus_sha256=corpus_sha,
            cache_dir=args.embedding_cache_dir,
            batch_size=args.batch_size,
        )
    )
    query_embeddings = encode_queries(
        model,
        query_texts,
        batch_size=args.batch_size,
    )
    embedding_time_ms = (
        time.perf_counter() - embedding_started
    ) * 1000.0

    bm25 = BM25Index(
        document_texts,
        k1=args.bm25_k1,
        b=args.bm25_b,
    )

    reports = []

    for mode in args.modes:
        if mode == "dense":
            def ranking_function(
                _query: str,
                query_embedding: np.ndarray,
                limit: int,
            ) -> list[tuple[int, float]]:
                return dense_rank(
                    query_embedding,
                    document_embeddings,
                    limit,
                )

        elif mode == "hybrid":
            def ranking_function(
                query: str,
                query_embedding: np.ndarray,
                limit: int,
            ) -> list[tuple[int, float]]:
                dense = dense_rank(
                    query_embedding,
                    document_embeddings,
                    args.candidate_k,
                )
                sparse = bm25.rank(
                    query,
                    args.candidate_k,
                )
                return reciprocal_rank_fusion(
                    [dense, sparse],
                    limit=limit,
                    rrf_constant=args.rrf_constant,
                )

        else:
            raise AssertionError(mode)

        configuration_name = mode
        if args.max_chunks_per_article > 0:
            configuration_name += (
                f"_article_cap_{args.max_chunks_per_article}"
            )
        if args.drop_missing_article_code:
            configuration_name += "_drop_missing_article_code"

        summary, per_question = evaluate_ranking(
            retriever_name=configuration_name,
            ranking_function=ranking_function,
            questions=questions,
            query_embeddings=query_embeddings,
            chunks=chunks,
            k_values=k_values,
            candidate_k=args.candidate_k,
            max_chunks_per_article=args.max_chunks_per_article,
            drop_missing_article_code=args.drop_missing_article_code,
        )

        report = {
            "schema_version": "retrieval-benchmark-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "retriever": {
                "name": configuration_name,
                "model": args.model,
                "candidate_k": args.candidate_k,
                "rrf_constant": (
                    args.rrf_constant if mode == "hybrid" else None
                ),
                "max_chunks_per_article": (
                    args.max_chunks_per_article
                ),
                "drop_missing_article_code": (
                    args.drop_missing_article_code
                ),
                "bm25_k1": (
                    args.bm25_k1 if mode == "hybrid" else None
                ),
                "bm25_b": (
                    args.bm25_b if mode == "hybrid" else None
                ),
            },
            "golden": {
                "path": str(args.golden),
                "schema_version": golden.get("schema_version"),
                "sha256": sha256_file(args.golden),
                "question_count": len(questions),
            },
            "corpus": {
                "path": str(args.chunks),
                "sha256": corpus_sha,
                "chunk_count": len(chunks),
            },
            "embedding": {
                "cache_path": str(cache_path),
                "cache_hit": cache_hit,
                "document_shape": list(document_embeddings.shape),
                "query_shape": list(query_embeddings.shape),
                "document_and_query_embedding_time_ms": round(
                    embedding_time_ms,
                    4,
                ),
            },
            "k_values": k_values,
            "summary": summary["summary"],
            "latency_ms_excluding_embedding": summary[
                "latency_ms_excluding_embedding"
            ],
            "per_question": per_question,
        }

        args.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = (
            args.output_dir
            / f"phapdien_{configuration_name}.json"
        )
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        reports.append((configuration_name, output_path, report))

    for name, output_path, report in reports:
        print(f"\nretriever={name}")
        for key, metrics in report["summary"].items():
            print(
                f"{key}: "
                f"article_any={metrics['any_article_hit']:.4f} "
                f"article_all={metrics['all_article_hit']:.4f} "
                f"article_recall={metrics['article_recall']:.4f} "
                f"article_mrr={metrics['article_mrr']:.4f} "
                f"evidence_any={metrics['any_evidence_hit']:.4f} "
                f"evidence_all={metrics['all_evidence_hit']:.4f} "
                f"evidence_recall={metrics['evidence_recall']:.4f}"
            )
        print(f"report={output_path}")


if __name__ == "__main__":
    main()
