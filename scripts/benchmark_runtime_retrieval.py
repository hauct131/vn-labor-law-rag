#!/usr/bin/env python3
"""Benchmark the exact production retrievers on locked Golden splits.

Dense reads FastEmbed E5 vectors from the configured Qdrant alias, sparse uses
the in-memory VnCoreNLP BM25 retriever, and hybrid uses the runtime weighted RRF
implementation. Tuning is allowed only on the dev split. Test evaluation needs
an explicit unlock and a selected configuration produced by dev tuning.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import logging
import math
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from backend.app.retrieval.dense_component import DenseRetriever
from backend.app.retrieval.hybrid_retriever import (
    HybridRetriever,
    reciprocal_rank_fusion,
)
from backend.app.retrieval.models import LegalRetriever, RetrievalHit
from backend.app.retrieval.sparse_retriever import (
    VnCoreNlpBm25Retriever,
    load_legal_chunks,
)
from backend.app.retrieval.vncorenlp_bm25 import load_segmenter


LOGGER = logging.getLogger("runtime_retrieval_benchmark")
METRIC_NAMES = (
    "any_article_hit",
    "all_article_hit",
    "article_recall",
    "article_mrr",
    "any_evidence_hit",
    "all_evidence_hit",
    "evidence_recall",
    "evidence_mrr",
)


class BenchmarkError(ValueError):
    """Raised for unsafe or inconsistent benchmark inputs."""


def _runtime_settings() -> Any:
    """Load application settings, with a stdlib-only fallback for dry-runs."""
    try:
        from backend.app.core.config import settings as application_settings

        return application_settings
    except ModuleNotFoundError as exc:
        if exc.name not in {"pydantic", "pydantic_settings"}:
            raise
        LOGGER.warning(
            "Application settings dependencies are unavailable; using exact "
            "environment/default values for static validation only."
        )
        return SimpleNamespace(
            legal_chunks_path=os.environ.get(
                "LEGAL_CHUNKS_PATH",
                "data/releases/labor-law-2026-07-28-candidate/chunks.jsonl",
            ),
            qdrant_url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
            qdrant_api_key=os.environ.get("QDRANT_API_KEY", ""),
            qdrant_collection=os.environ.get(
                "QDRANT_COLLECTION", "labor_law_active"
            ),
            dense_embedding_model=os.environ.get(
                "DENSE_EMBEDDING_MODEL", "intfloat/multilingual-e5-large"
            ),
            dense_vector_name=os.environ.get("DENSE_VECTOR_NAME", "dense"),
            dense_vector_size=int(os.environ.get("DENSE_VECTOR_SIZE", "1024")),
            embedding_threads=int(os.environ.get("EMBEDDING_THREADS", "6")),
            fastembed_cache_dir=os.environ.get("FASTEMBED_CACHE_DIR", ""),
            vncorenlp_model_dir=os.environ.get(
                "VNCORENLP_MODEL_DIR", "models/vncorenlp"
            ),
            bm25_k=float(os.environ.get("BM25_K", "1.2")),
            bm25_b=float(os.environ.get("BM25_B", "0.75")),
            retrieval_candidate_k=int(
                os.environ.get("RETRIEVAL_CANDIDATE_K", "20")
            ),
            hybrid_rrf_k=int(os.environ.get("HYBRID_RRF_K", "60")),
            hybrid_dense_weight=float(
                os.environ.get("HYBRID_DENSE_WEIGHT", "1.0")
            ),
            hybrid_sparse_weight=float(
                os.environ.get("HYBRID_SPARSE_WEIGHT", "1.0")
            ),
        )


@dataclass(frozen=True)
class QueryRun:
    question: Mapping[str, Any]
    hits: Sequence[RetrievalHit]
    latency_ms: float


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _safe_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.username is None and parsed.password is None:
        return url
    hostname = parsed.hostname or ""
    if parsed.port:
        hostname += f":{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, parsed.query, ""))


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _reciprocal_rank(expected: set[str], ranked: Sequence[str]) -> float:
    for rank, value in enumerate(ranked, 1):
        if value in expected:
            return 1.0 / rank
    return 0.0


def metric_values(
    question: Mapping[str, Any],
    hits: Sequence[RetrievalHit],
    k: int,
) -> dict[str, float]:
    expected_articles = {
        str(value) for value in question.get("expected_article_codes", []) if value
    }
    expected_evidence = {
        str(value) for value in question.get("evidence_chunk_ids", []) if value
    }
    top = list(hits[:k])
    ranked_articles = [
        str(hit.payload.get("article_code") or "") for hit in top
    ]
    ranked_ids = [hit.chunk_id for hit in top]
    actual_articles = {value for value in ranked_articles if value}
    actual_ids = set(ranked_ids)

    article_recall = (
        len(expected_articles & actual_articles) / len(expected_articles)
        if expected_articles
        else 1.0
    )
    evidence_recall = (
        len(expected_evidence & actual_ids) / len(expected_evidence)
        if expected_evidence
        else 1.0
    )
    return {
        "any_article_hit": float(
            not expected_articles or bool(expected_articles & actual_articles)
        ),
        "all_article_hit": float(
            not expected_articles or expected_articles <= actual_articles
        ),
        "article_recall": article_recall,
        "article_mrr": _reciprocal_rank(expected_articles, ranked_articles),
        "any_evidence_hit": float(
            not expected_evidence or bool(expected_evidence & actual_ids)
        ),
        "all_evidence_hit": float(
            not expected_evidence or expected_evidence <= actual_ids
        ),
        "evidence_recall": evidence_recall,
        "evidence_mrr": _reciprocal_rank(expected_evidence, ranked_ids),
    }


def _mean_metrics(rows: Sequence[Mapping[str, float]]) -> dict[str, float]:
    if not rows:
        return {name: 0.0 for name in METRIC_NAMES}
    return {
        name: round(statistics.fmean(row[name] for row in rows), 6)
        for name in METRIC_NAMES
    }


def summarize_runs(
    runs: Sequence[QueryRun],
    k_values: Sequence[int],
) -> dict[str, Any]:
    aggregate = {
        k: [metric_values(run.question, run.hits, k) for run in runs]
        for k in k_values
    }
    latency = [run.latency_ms for run in runs]
    summary: dict[str, Any] = {
        "question_count": len(runs),
        "metrics": {
            f"at_{k}": _mean_metrics(aggregate[k]) for k in k_values
        },
        "latency_ms": {
            "mean": round(statistics.fmean(latency), 3) if latency else 0.0,
            "p50": round(_percentile(latency, 0.50), 3),
            "p95": round(_percentile(latency, 0.95), 3),
            "min": round(min(latency), 3) if latency else 0.0,
            "max": round(max(latency), 3) if latency else 0.0,
        },
    }

    if 10 in k_values:
        single_rows = []
        multi_rows = []
        by_category: dict[str, list[dict[str, float]]] = {}
        for run, row in zip(runs, aggregate[10], strict=True):
            expected = run.question.get("expected_article_codes", [])
            (single_rows if len(expected) <= 1 else multi_rows).append(row)
            category = str(run.question.get("category") or "unknown")
            by_category.setdefault(category, []).append(row)
        summary["stratified_at_10"] = {
            "single_article": _mean_metrics(single_rows),
            "multi_article": _mean_metrics(multi_rows),
            "by_category": {
                category: _mean_metrics(rows)
                for category, rows in sorted(by_category.items())
            },
        }
    return summary


def _serialized_hit(hit: RetrievalHit) -> dict[str, Any]:
    return {
        "rank": hit.rank,
        "score": round(float(hit.score), 8),
        "chunk_id": hit.chunk_id,
        "article_code": hit.payload.get("article_code"),
        "article_title": hit.payload.get("article_title"),
        "clause_number": hit.payload.get("clause_number"),
        "retrieval_origin": hit.retrieval_origin,
        "component_ranks": dict(hit.component_ranks),
        "component_scores": {
            key: round(float(value), 8)
            for key, value in hit.component_scores.items()
        },
        "content_preview": " ".join(hit.content.split())[:400],
    }


def _serialized_run(run: QueryRun, k_values: Sequence[int]) -> dict[str, Any]:
    return {
        "question_id": run.question["id"],
        "category": run.question.get("category"),
        "question": run.question["question"],
        "expected_article_codes": run.question.get("expected_article_codes", []),
        "expected_evidence_chunk_ids": run.question.get("evidence_chunk_ids", []),
        "latency_ms": round(run.latency_ms, 3),
        "metrics": {
            f"at_{k}": {
                name: round(value, 6)
                for name, value in metric_values(run.question, run.hits, k).items()
            }
            for k in k_values
        },
        "retrieved": [_serialized_hit(hit) for hit in run.hits],
    }


def load_locked_split(path: Path, expected_role: str) -> dict[str, Any]:
    if not path.is_file():
        raise BenchmarkError(f"golden split not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"invalid golden split JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise BenchmarkError("golden split must be an object with questions")
    if payload.get("locked") is not True:
        raise BenchmarkError("runtime benchmark requires a locked golden split")
    actual_role = payload.get("split", {}).get("role")
    if actual_role != expected_role:
        raise BenchmarkError(
            f"golden split role mismatch: expected {expected_role}, got {actual_role}"
        )
    questions = payload["questions"]
    if not questions or any(
        question.get("benchmark_split") != expected_role for question in questions
    ):
        raise BenchmarkError("question split markers are missing or inconsistent")
    return payload


def validate_corpus_binding(
    golden: Mapping[str, Any],
    *,
    chunks: Sequence[Mapping[str, Any]],
    corpus_sha256: str,
) -> None:
    declared = golden.get("corpus", {})
    if declared.get("sha256") != corpus_sha256:
        raise BenchmarkError(
            "golden/corpus SHA-256 mismatch: expected "
            f"{declared.get('sha256')}, got {corpus_sha256}"
        )
    if declared.get("chunk_count") != len(chunks):
        raise BenchmarkError(
            "golden/corpus chunk count mismatch: expected "
            f"{declared.get('chunk_count')}, got {len(chunks)}"
        )
    available_articles = {
        str(chunk.get("article_code"))
        for chunk in chunks
        if chunk.get("article_code")
    }
    missing = sorted({
        str(code)
        for question in golden["questions"]
        for code in question.get("expected_article_codes", [])
        if code not in available_articles
    })
    if missing:
        raise BenchmarkError(
            "expected articles missing from corpus: " + ", ".join(missing)
        )


def _run_retriever(
    retriever: LegalRetriever,
    questions: Sequence[Mapping[str, Any]],
    top_k: int,
) -> list[QueryRun]:
    runs = []
    for index, question in enumerate(questions, 1):
        LOGGER.info("[%d/%d] %s", index, len(questions), question["id"])
        started = time.perf_counter()
        hits = retriever.retrieve(str(question["question"]), top_k=top_k)
        elapsed = (time.perf_counter() - started) * 1000
        runs.append(QueryRun(question=question, hits=hits, latency_ms=elapsed))
    return runs


def _fused_runs(
    sparse_runs: Sequence[QueryRun],
    dense_runs: Sequence[QueryRun],
    *,
    top_k: int,
    candidate_k: int,
    rrf_k: int,
    sparse_weight: float,
    dense_weight: float,
) -> list[QueryRun]:
    output = []
    for sparse, dense in zip(sparse_runs, dense_runs, strict=True):
        started = time.perf_counter()
        hits = reciprocal_rank_fusion(
            {
                "sparse": sparse.hits[:candidate_k],
                "dense": dense.hits[:candidate_k],
            },
            top_k=top_k,
            rrf_k=rrf_k,
            weights={"sparse": sparse_weight, "dense": dense_weight},
        )
        fusion_ms = (time.perf_counter() - started) * 1000
        output.append(QueryRun(
            question=sparse.question,
            hits=hits,
            latency_ms=sparse.latency_ms + dense.latency_ms + fusion_ms,
        ))
    return output


def _method_report(
    *,
    method: str,
    role: str,
    config: Mapping[str, Any],
    runs: Sequence[QueryRun],
    k_values: Sequence[int],
) -> dict[str, Any]:
    return {
        "schema_version": "runtime-retrieval-benchmark-v1",
        "status": "completed",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "split_role": role,
        "method": method,
        "config": dict(config),
        "summary": summarize_runs(runs, k_values),
        "questions": [_serialized_run(run, k_values) for run in runs],
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _comparison(
    dense_runs: Sequence[QueryRun],
    hybrid_runs: Sequence[QueryRun],
    k: int = 10,
) -> dict[str, Any]:
    improved = []
    regressed = []
    unchanged = []
    for dense, hybrid in zip(dense_runs, hybrid_runs, strict=True):
        dense_value = metric_values(dense.question, dense.hits, k)["article_recall"]
        hybrid_value = metric_values(hybrid.question, hybrid.hits, k)["article_recall"]
        record = {
            "question_id": dense.question["id"],
            "dense_article_recall": round(dense_value, 6),
            "hybrid_article_recall": round(hybrid_value, 6),
            "delta": round(hybrid_value - dense_value, 6),
        }
        if hybrid_value > dense_value:
            improved.append(record)
        elif hybrid_value < dense_value:
            regressed.append(record)
        else:
            unchanged.append(record)
    return {
        "metric": f"article_recall_at_{k}",
        "improved_count": len(improved),
        "regressed_count": len(regressed),
        "unchanged_count": len(unchanged),
        "improved": improved,
        "regressed": regressed,
    }


def _selection_key(trial: Mapping[str, Any]) -> tuple[Any, ...]:
    at_10 = trial["summary"]["metrics"]["at_10"]
    config = trial["config"]
    article_score = (
        0.45 * at_10["all_article_hit"]
        + 0.35 * at_10["article_recall"]
        + 0.15 * at_10["article_mrr"]
        + 0.05 * at_10["any_article_hit"]
    )
    return (
        article_score,
        at_10["all_article_hit"],
        at_10["article_recall"],
        at_10["article_mrr"],
        -config["candidate_k"],
        -config["rrf_k"],
        config["dense_weight"],
    )


def _selected_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BenchmarkError(f"dev tuning report not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"invalid dev tuning report: {exc}") from exc
    if payload.get("split_role") != "dev" or not isinstance(
        payload.get("selected_config"), dict
    ):
        raise BenchmarkError(
            "test evaluation requires a dev tuning report with selected_config"
        )
    return dict(payload["selected_config"])


def _base_config(args: argparse.Namespace, golden: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "golden": str(args.golden),
        "golden_split_version": golden.get("split", {}).get("version"),
        "corpus": str(args.chunks),
        "corpus_sha256": golden.get("corpus", {}).get("sha256"),
        "qdrant_url": _safe_endpoint(args.qdrant_url),
        "qdrant_api_key_configured": bool(args.qdrant_api_key),
        "collection": args.collection,
        "dense_model": args.dense_model,
        "dense_vector_name": args.dense_vector_name,
        "dense_size": args.dense_size,
        "vncorenlp_model_dir": str(args.vncorenlp_model_dir),
        "bm25_k": args.bm25_k,
        "bm25_b": args.bm25_b,
        "k_values": args.k,
        "package_versions": {
            "fastembed": _package_version("fastembed"),
            "qdrant-client": _package_version("qdrant-client"),
            "py-vncorenlp": _package_version("py-vncorenlp"),
        },
        "writes_qdrant": False,
        "evidence_metrics_decision_role": "secondary_only",
    }


def _create_components(
    args: argparse.Namespace,
    chunks: Sequence[Mapping[str, Any]],
    corpus_sha256: str,
    *,
    top_k: int,
) -> tuple[VnCoreNlpBm25Retriever, DenseRetriever]:
    LOGGER.info("Loading VnCoreNLP and building the production sparse index")
    sparse = VnCoreNlpBm25Retriever.from_chunks(
        chunks,
        segmenter=load_segmenter(args.vncorenlp_model_dir),
        default_top_k=top_k,
        k=args.bm25_k,
        b=args.bm25_b,
        corpus_sha256=corpus_sha256,
    )
    dense = DenseRetriever(
        qdrant_url=args.qdrant_url,
        collection_name=args.collection,
        api_key=args.qdrant_api_key,
        model_name=args.dense_model,
        vector_name=args.dense_vector_name,
        vector_size=args.dense_size,
        default_top_k=top_k,
        threads=args.threads,
        cache_dir=args.fastembed_cache_dir,
        expected_corpus_sha256=corpus_sha256,
    )
    LOGGER.info("Warming the production dense retriever")
    dense.warmup()
    return sparse, dense


def _write_markdown_summary(output_dir: Path) -> None:
    rows = []
    for role in ("dev", "test"):
        for method in ("dense", "sparse", "hybrid"):
            path = output_dir / f"{role}_{method}.json"
            if not path.is_file():
                continue
            report = json.loads(path.read_text(encoding="utf-8"))
            at_10 = report["summary"]["metrics"].get("at_10")
            if not at_10:
                continue
            latency = report["summary"]["latency_ms"]
            multi = report["summary"].get("stratified_at_10", {}).get(
                "multi_article", {}
            )
            rows.append((
                role,
                method,
                at_10["article_recall"],
                at_10["all_article_hit"],
                at_10["article_mrr"],
                multi.get("article_recall", 0.0),
                latency["p50"],
                latency["p95"],
            ))

    lines = [
        "# Production-equivalent retrieval benchmark",
        "",
        "The benchmark uses the locked runtime split. Evidence metrics are "
        "reported in JSON but are not used for model selection.",
        "",
        "| Split | Method | Article Recall@10 | All-Article Hit@10 | Article MRR@10 | Multi-Article Recall@10 | p50 ms | p95 ms |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row[0]} | {row[1]} | {row[2]:.4f} | {row[3]:.4f} | "
            f"{row[4]:.4f} | {row[5]:.4f} | {row[6]:.1f} | {row[7]:.1f} |"
        )
    if not any(row[0] == "test" for row in rows):
        lines += [
            "",
            "> Test has not been evaluated. Run it once only after selecting "
            "the dev configuration.",
        ]
    path = output_dir / "benchmark_summary.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_tuning(
    args: argparse.Namespace,
    golden: Mapping[str, Any],
    chunks: Sequence[Mapping[str, Any]],
    corpus_sha256: str,
) -> dict[str, Any]:
    if args.split_role != "dev":
        raise BenchmarkError("hybrid tuning is permitted only on the dev split")
    if 10 not in args.k:
        raise BenchmarkError("tuning requires --k to include 10")
    max_candidates = max(max(args.candidate_k_grid), max(args.k))
    sparse, dense = _create_components(
        args, chunks, corpus_sha256, top_k=max_candidates
    )
    questions = golden["questions"]
    sparse_runs = _run_retriever(sparse, questions, max_candidates)
    dense_runs = _run_retriever(dense, questions, max_candidates)
    base = _base_config(args, golden)

    trials = []
    for candidate_k in sorted(set(args.candidate_k_grid)):
        for dense_weight in sorted(set(args.dense_weight_grid)):
            dense_weight = round(float(dense_weight), 6)
            sparse_weight = round(1.0 - dense_weight, 6)
            if dense_weight <= 0 or sparse_weight <= 0:
                raise BenchmarkError("dense weights must be strictly between 0 and 1")
            for rrf_k in sorted(set(args.rrf_k_grid)):
                config = {
                    "candidate_k": candidate_k,
                    "rrf_k": rrf_k,
                    "dense_weight": dense_weight,
                    "sparse_weight": sparse_weight,
                }
                name = (
                    f"cand{candidate_k}_dw{dense_weight:.2f}_"
                    f"sw{sparse_weight:.2f}_rrf{rrf_k}"
                ).replace(".", "p")
                fused = _fused_runs(
                    sparse_runs,
                    dense_runs,
                    top_k=max(args.k),
                    **config,
                )
                summary = summarize_runs(fused, args.k)
                summary["latency_note"] = (
                    "Quality-search upper bound from component retrieval at "
                    f"max candidate_k={max_candidates}; not used for selection."
                )
                at_10 = summary["metrics"]["at_10"]
                article_score = (
                    0.45 * at_10["all_article_hit"]
                    + 0.35 * at_10["article_recall"]
                    + 0.15 * at_10["article_mrr"]
                    + 0.05 * at_10["any_article_hit"]
                )
                trials.append({
                    "name": name,
                    "config": config,
                    "article_selection_score": round(article_score, 6),
                    "summary": summary,
                })

    trials.sort(key=_selection_key, reverse=True)
    selected = trials[0]

    # Measure the final three methods at their real runtime limits. Trial
    # latency above is an upper bound because candidates were precomputed at
    # the largest grid value.
    final_sparse_runs = _run_retriever(sparse, questions, max(args.k))
    final_dense_runs = _run_retriever(dense, questions, max(args.k))
    selected_hybrid = HybridRetriever(
        sparse_retriever=sparse,
        dense_retriever=dense,
        default_top_k=max(args.k),
        candidate_k=selected["config"]["candidate_k"],
        rrf_k=selected["config"]["rrf_k"],
        sparse_weight=selected["config"]["sparse_weight"],
        dense_weight=selected["config"]["dense_weight"],
    )
    selected_runs = _run_retriever(selected_hybrid, questions, max(args.k))

    dense_report = _method_report(
        method="dense",
        role="dev",
        config=base,
        runs=final_dense_runs,
        k_values=args.k,
    )
    sparse_report = _method_report(
        method="sparse",
        role="dev",
        config=base,
        runs=final_sparse_runs,
        k_values=args.k,
    )
    _atomic_json(args.output_dir / "dev_dense.json", dense_report)
    _atomic_json(args.output_dir / "dev_sparse.json", sparse_report)

    hybrid_config = {**base, **selected["config"]}
    hybrid_report = _method_report(
        method="hybrid",
        role="dev",
        config=hybrid_config,
        runs=selected_runs,
        k_values=args.k,
    )
    hybrid_report["dense_comparison"] = _comparison(
        final_dense_runs, selected_runs
    )
    _atomic_json(args.output_dir / "dev_hybrid.json", hybrid_report)

    tuning_report = {
        "schema_version": "runtime-hybrid-tuning-v1",
        "status": "completed",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "split_role": "dev",
        "selection_metric": (
            "0.45*all_article_hit@10 + 0.35*article_recall@10 + "
            "0.15*article_mrr@10 + 0.05*any_article_hit@10"
        ),
        "tie_breakers": [
            "all_article_hit_at_10",
            "article_recall_at_10",
            "article_mrr_at_10",
            "lower_candidate_k",
            "lower_rrf_k",
        ],
        "selected_config": selected["config"],
        "selected_trial": selected,
        "dense_comparison": _comparison(final_dense_runs, selected_runs),
        "trials": trials,
        "base_config": base,
    }
    trials_path = args.output_dir / "dev_hybrid_trials.json"
    _atomic_json(trials_path, tuning_report)
    _write_markdown_summary(args.output_dir)
    return {
        "status": "completed",
        "phase": "tune",
        "selected_config": selected["config"],
        "trial_count": len(trials),
        "output": str(trials_path),
    }


def run_evaluation(
    args: argparse.Namespace,
    golden: Mapping[str, Any],
    chunks: Sequence[Mapping[str, Any]],
    corpus_sha256: str,
) -> dict[str, Any]:
    selected: dict[str, Any] | None = None
    if args.split_role == "test":
        if not args.allow_test_evaluation:
            raise BenchmarkError(
                "test split is locked; pass --allow-test-evaluation only after dev tuning"
            )
        if args.hybrid_config is None:
            raise BenchmarkError("test evaluation requires --hybrid-config")
        selected = _selected_config(args.hybrid_config)
    elif args.hybrid_config is not None:
        selected = _selected_config(args.hybrid_config)

    methods = list(dict.fromkeys(args.methods))
    max_k = max(args.k)
    candidate_k = int((selected or {}).get("candidate_k", args.candidate_k))
    rrf_k = int((selected or {}).get("rrf_k", args.rrf_k))
    dense_weight = float((selected or {}).get("dense_weight", args.dense_weight))
    sparse_weight = float((selected or {}).get("sparse_weight", args.sparse_weight))
    top_k = max(max_k, candidate_k)
    sparse, dense = _create_components(args, chunks, corpus_sha256, top_k=top_k)
    base = _base_config(args, golden)
    questions = golden["questions"]
    reports: dict[str, dict[str, Any]] = {}
    runs_by_method: dict[str, list[QueryRun]] = {}

    if "sparse" in methods:
        runs_by_method["sparse"] = _run_retriever(sparse, questions, max_k)
    if "dense" in methods:
        runs_by_method["dense"] = _run_retriever(dense, questions, max_k)
    if "hybrid" in methods:
        hybrid = HybridRetriever(
            sparse_retriever=sparse,
            dense_retriever=dense,
            default_top_k=max_k,
            candidate_k=candidate_k,
            rrf_k=rrf_k,
            sparse_weight=sparse_weight,
            dense_weight=dense_weight,
        )
        runs_by_method["hybrid"] = _run_retriever(hybrid, questions, max_k)

    for method, runs in runs_by_method.items():
        config = dict(base)
        if method == "hybrid":
            config.update({
                "candidate_k": candidate_k,
                "rrf_k": rrf_k,
                "dense_weight": dense_weight,
                "sparse_weight": sparse_weight,
                "selected_from_dev": selected is not None,
            })
        report = _method_report(
            method=method,
            role=args.split_role,
            config=config,
            runs=runs,
            k_values=args.k,
        )
        reports[method] = report
        _atomic_json(args.output_dir / f"{args.split_role}_{method}.json", report)

    if "dense" in runs_by_method and "hybrid" in runs_by_method:
        comparison = _comparison(
            runs_by_method["dense"], runs_by_method["hybrid"]
        )
        reports["hybrid"]["dense_comparison"] = comparison
        _atomic_json(
            args.output_dir / f"{args.split_role}_hybrid.json",
            reports["hybrid"],
        )

    _write_markdown_summary(args.output_dir)
    return {
        "status": "completed",
        "phase": "evaluate",
        "split_role": args.split_role,
        "methods": methods,
        "outputs": {
            method: str(args.output_dir / f"{args.split_role}_{method}.json")
            for method in methods
        },
    }


def build_parser() -> argparse.ArgumentParser:
    runtime_settings = _runtime_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("evaluate", "tune"), default="evaluate")
    parser.add_argument("--split-role", choices=("dev", "test"), default="dev")
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path("data/evaluation/splits/golden_v3_dev.json"),
    )
    parser.add_argument(
        "--chunks", type=Path, default=Path(runtime_settings.legal_chunks_path)
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/evaluation/runtime-benchmark"),
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=("dense", "sparse", "hybrid"),
        default=("dense", "sparse", "hybrid"),
    )
    parser.add_argument("--k", type=int, nargs="+", default=(5, 10, 20))
    parser.add_argument("--qdrant-url", default=runtime_settings.qdrant_url)
    parser.add_argument("--qdrant-api-key", default=runtime_settings.qdrant_api_key)
    parser.add_argument("--collection", default=runtime_settings.qdrant_collection)
    parser.add_argument("--dense-model", default=runtime_settings.dense_embedding_model)
    parser.add_argument("--dense-vector-name", default=runtime_settings.dense_vector_name)
    parser.add_argument("--dense-size", type=int, default=runtime_settings.dense_vector_size)
    parser.add_argument("--threads", type=int, default=runtime_settings.embedding_threads)
    parser.add_argument(
        "--fastembed-cache-dir",
        type=Path,
        default=(
            Path(runtime_settings.fastembed_cache_dir)
            if runtime_settings.fastembed_cache_dir
            else None
        ),
    )
    parser.add_argument(
        "--vncorenlp-model-dir",
        type=Path,
        default=Path(runtime_settings.vncorenlp_model_dir),
    )
    parser.add_argument("--bm25-k", type=float, default=runtime_settings.bm25_k)
    parser.add_argument("--bm25-b", type=float, default=runtime_settings.bm25_b)
    parser.add_argument(
        "--candidate-k", type=int, default=runtime_settings.retrieval_candidate_k
    )
    parser.add_argument("--rrf-k", type=int, default=runtime_settings.hybrid_rrf_k)
    parser.add_argument(
        "--dense-weight", type=float, default=runtime_settings.hybrid_dense_weight
    )
    parser.add_argument(
        "--sparse-weight", type=float, default=runtime_settings.hybrid_sparse_weight
    )
    parser.add_argument("--candidate-k-grid", type=int, nargs="+", default=(20, 50))
    parser.add_argument(
        "--dense-weight-grid", type=float, nargs="+", default=(0.6, 0.7, 0.8, 0.9)
    )
    parser.add_argument("--rrf-k-grid", type=int, nargs="+", default=(30, 60))
    parser.add_argument("--hybrid-config", type=Path)
    parser.add_argument("--allow-test-evaluation", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not args.k or any(value <= 0 for value in args.k):
        raise BenchmarkError("--k values must be greater than zero")
    args.k = sorted(set(args.k))
    if args.candidate_k <= 0 or any(value <= 0 for value in args.candidate_k_grid):
        raise BenchmarkError("candidate-k values must be greater than zero")
    if args.rrf_k < 0 or any(value < 0 for value in args.rrf_k_grid):
        raise BenchmarkError("rrf-k values must be zero or greater")
    if args.dense_size <= 0 or args.threads <= 0:
        raise BenchmarkError("dense-size and threads must be greater than zero")
    if args.phase == "tune" and args.split_role != "dev":
        raise BenchmarkError("tuning is permitted only on dev")


def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    golden = load_locked_split(args.golden, args.split_role)
    chunks, corpus_sha256 = load_legal_chunks(
        args.chunks,
        expected_chunks=golden.get("corpus", {}).get("chunk_count"),
        expected_sha256=golden.get("corpus", {}).get("sha256"),
    )
    validate_corpus_binding(golden, chunks=chunks, corpus_sha256=corpus_sha256)

    if args.split_role == "test" and not args.allow_test_evaluation:
        raise BenchmarkError(
            "test split is locked; pass --allow-test-evaluation only after dev tuning"
        )
    if args.dry_run:
        return {
            "status": "dry_run_pass",
            "phase": args.phase,
            "split_role": args.split_role,
            "question_count": len(golden["questions"]),
            "corpus_sha256": corpus_sha256,
            "qdrant_api_key_configured": bool(args.qdrant_api_key),
        }
    if args.phase == "tune":
        return run_tuning(args, golden, chunks, corpus_sha256)
    return run_evaluation(args, golden, chunks, corpus_sha256)


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        result = run(args)
    except Exception as exc:
        LOGGER.error("Runtime benchmark failed: %s", exc)
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
