#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Sequence

from app.core.config import settings
from app.retrieval.hybrid_retriever import (
    create_hybrid_retriever,
    get_stable_document_key,
    select_generation_context,
)
from app.retrieval.models import RetrievalHit


def load_questions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = None
        for key in ("questions", "items", "cases"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break
        if rows is None:
            raise ValueError(f"Unsupported golden structure: {path}")
    else:
        raise ValueError(f"Unsupported golden structure: {path}")

    normalized = []

    for row in rows:
        item = dict(row)

        item["question_id"] = str(
            item.get("question_id")
            or item.get("id")
            or ""
        ).strip()

        item["expected_evidence_chunk_ids"] = list(
            item.get("expected_evidence_chunk_ids")
            or item.get("evidence_chunk_ids")
            or []
        )

        normalized.append(item)

    return normalized


def article_code(hit: RetrievalHit) -> str:
    return str(hit.payload.get("article_code") or "").strip()


def metrics(
    hits: Sequence[RetrievalHit],
    question: dict[str, Any],
) -> dict[str, float]:
    expected_articles = {
        str(x).strip()
        for x in question.get("expected_article_codes", [])
        if str(x).strip()
    }
    expected_chunks = {
        str(x).strip()
        for x in question.get("expected_evidence_chunk_ids", [])
        if str(x).strip()
    }

    retrieved_articles = [
        article_code(hit)
        for hit in hits
        if article_code(hit)
    ]
    retrieved_chunks = [hit.chunk_id for hit in hits]

    article_set = set(retrieved_articles)
    chunk_set = set(retrieved_chunks)

    article_matches = expected_articles & article_set
    evidence_matches = expected_chunks & chunk_set

    article_mrr = 0.0
    for rank, code in enumerate(retrieved_articles, start=1):
        if code in expected_articles:
            article_mrr = 1.0 / rank
            break

    evidence_mrr = 0.0
    for rank, chunk_id in enumerate(retrieved_chunks, start=1):
        if chunk_id in expected_chunks:
            evidence_mrr = 1.0 / rank
            break

    return {
        "any_article_hit": float(bool(article_matches)),
        "all_article_hit": float(
            bool(expected_articles)
            and expected_articles.issubset(article_set)
        ),
        "article_recall": (
            len(article_matches) / len(expected_articles)
            if expected_articles else 0.0
        ),
        "article_mrr": article_mrr,
        "any_evidence_hit": float(bool(evidence_matches)),
        "all_evidence_hit": float(
            bool(expected_chunks)
            and expected_chunks.issubset(chunk_set)
        ),
        "evidence_recall": (
            len(evidence_matches) / len(expected_chunks)
            if expected_chunks else 0.0
        ),
        "evidence_mrr": evidence_mrr,
    }


def diversity(hits: Sequence[RetrievalHit]) -> dict[str, float]:
    if not hits:
        return {
            "unique_articles": 0.0,
            "unique_documents": 0.0,
            "duplicate_article_rate": 0.0,
            "max_document_concentration": 0.0,
        }

    articles = [article_code(hit) for hit in hits if article_code(hit)]
    documents = [
        get_stable_document_key(hit.payload)
        for hit in hits
    ]

    doc_counts = Counter(documents)

    return {
        "unique_articles": float(len(set(articles))),
        "unique_documents": float(len(set(documents))),
        "duplicate_article_rate": (
            1.0 - len(set(articles)) / len(articles)
            if articles else 0.0
        ),
        "max_document_concentration": (
            max(doc_counts.values()) / len(hits)
            if doc_counts else 0.0
        ),
    }


def aggregate(
    rows: list[dict[str, Any]],
    key: str,
) -> dict[str, float]:
    metric_names = rows[0][key]["metrics"].keys()
    diversity_names = rows[0][key]["diversity"].keys()

    result = {
        name: mean(row[key]["metrics"][name] for row in rows)
        for name in metric_names
    }

    result.update({
        name: mean(row[key]["diversity"][name] for row in rows)
        for name in diversity_names
    })

    return {
        name: round(value, 6)
        for name, value in result.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--collection", default="labor_law_dev")
    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--context-k", type=int, default=10)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--dense-weight", type=float, default=0.9)
    parser.add_argument("--sparse-weight", type=float, default=0.1)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    cfg_updates = {
        "qdrant_url": args.qdrant_url,
        "qdrant_collection": args.collection,
        "retrieval_candidate_k": args.candidate_k,
        "generation_context_k": args.context_k,
        "hybrid_rrf_k": args.rrf_k,
        "hybrid_dense_weight": args.dense_weight,
        "hybrid_sparse_weight": args.sparse_weight,
    }

    if hasattr(settings, "model_copy"):
        cfg = settings.model_copy(update=cfg_updates)
    else:
        cfg = settings.copy(update=cfg_updates)

    retriever = create_hybrid_retriever(
        cfg,
        default_top_k=args.context_k,
        candidate_k=args.candidate_k,
        rrf_k=args.rrf_k,
        dense_weight=args.dense_weight,
        sparse_weight=args.sparse_weight,
    )

    questions = load_questions(args.golden)
    rows: list[dict[str, Any]] = []

    top5_violations = 0
    changed_contexts = 0

    for index, question in enumerate(questions, start=1):
        query = str(question["question"]).strip()

        pool = retriever.retrieve_candidates(
            query,
            candidate_k=args.candidate_k,
        )

        raw = list(pool[: args.context_k])

        selected = select_generation_context(
            pool,
            generation_context_k=args.context_k,
        )

        raw_ids = [hit.chunk_id for hit in raw]
        selected_ids = [hit.chunk_id for hit in selected]

        if raw_ids[:5] != selected_ids[:5]:
            top5_violations += 1

        changed = raw_ids != selected_ids
        changed_contexts += int(changed)

        entered = [
            hit.chunk_id
            for hit in selected
            if hit.chunk_id not in raw_ids
        ]
        dropped = [
            hit.chunk_id
            for hit in raw
            if hit.chunk_id not in selected_ids
        ]

        rows.append({
            "question_id": question.get("question_id", f"q{index:03d}"),
            "category": question.get("category"),
            "question": query,
            "expected_article_codes": question.get(
                "expected_article_codes", []
            ),
            "raw": {
                "metrics": metrics(raw, question),
                "diversity": diversity(raw),
                "article_codes": [
                    article_code(hit) for hit in raw
                ],
                "chunk_ids": raw_ids,
            },
            "bounded": {
                "metrics": metrics(selected, question),
                "diversity": diversity(selected),
                "article_codes": [
                    article_code(hit) for hit in selected
                ],
                "chunk_ids": selected_ids,
            },
            "changed": changed,
            "entered_chunk_ids": entered,
            "dropped_chunk_ids": dropped,
        })

    raw_summary = aggregate(rows, "raw")
    bounded_summary = aggregate(rows, "bounded")

    delta = {
        key: round(
            bounded_summary[key] - raw_summary[key],
            6,
        )
        for key in raw_summary
    }

    improved_recall = []
    regressed_recall = []

    for row in rows:
        d = (
            row["bounded"]["metrics"]["article_recall"]
            - row["raw"]["metrics"]["article_recall"]
        )
        if d > 1e-12:
            improved_recall.append({
                "question_id": row["question_id"],
                "delta": round(d, 6),
            })
        elif d < -1e-12:
            regressed_recall.append({
                "question_id": row["question_id"],
                "delta": round(d, 6),
            })

    report = {
        "schema_version": "bounded-reranking-ablation-v1",
        "status": "completed",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "split_role": "dev",
        "question_count": len(rows),
        "config": {
            "golden": str(args.golden),
            "collection": args.collection,
            "candidate_k": args.candidate_k,
            "generation_context_k": args.context_k,
            "rrf_k": args.rrf_k,
            "dense_weight": args.dense_weight,
            "sparse_weight": args.sparse_weight,
        },
        "variants": {
            "raw": "Weighted-RRF candidate pool top generation_context_k",
            "bounded": (
                "Preserve fused top-5; bounded metadata reranking "
                "for remaining generation-context slots"
            ),
        },
        "checks": {
            "top5_preservation_violations": top5_violations,
            "changed_context_count": changed_contexts,
            "unchanged_context_count": len(rows) - changed_contexts,
        },
        "summary": {
            "raw": raw_summary,
            "bounded": bounded_summary,
            "delta_bounded_minus_raw": delta,
        },
        "article_recall_changes": {
            "improved_count": len(improved_recall),
            "regressed_count": len(regressed_recall),
            "improved": improved_recall,
            "regressed": regressed_recall,
        },
        "questions": rows,
    }

    json_path = args.output_dir / "bounded_reranking_ablation.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    interesting = [
        "any_article_hit",
        "all_article_hit",
        "article_recall",
        "article_mrr",
        "evidence_recall",
        "unique_articles",
        "unique_documents",
        "duplicate_article_rate",
        "max_document_concentration",
    ]

    lines = [
        "# Bounded Reranking Ablation",
        "",
        (
            f"DEV questions: {len(rows)}; candidate_k={args.candidate_k}; "
            f"context_k={args.context_k}; RRF={args.rrf_k}; "
            f"dense/sparse={args.dense_weight}/{args.sparse_weight}."
        ),
        "",
        "| Metric | Raw RRF@10 | Bounded@10 | Delta |",
        "|---|---:|---:|---:|",
    ]

    for name in interesting:
        lines.append(
            f"| {name} "
            f"| {raw_summary[name]:.6f} "
            f"| {bounded_summary[name]:.6f} "
            f"| {delta[name]:+.6f} |"
        )

    lines += [
        "",
        (
            f"- Context changed for **{changed_contexts}/{len(rows)}** "
            "questions."
        ),
        (
            "- Top-5 preservation violations: "
            f"**{top5_violations}**."
        ),
        (
            "- Article Recall improved/regressed: "
            f"**{len(improved_recall)}/{len(regressed_recall)}**."
        ),
        "",
        (
            "Interpretation rule: bounded reranking is considered safe when "
            "top-5 preservation violations = 0 and it does not materially "
            "degrade article/evidence coverage. Diversity metrics are "
            "secondary context-selection diagnostics."
        ),
    ]

    (args.output_dir / "benchmark_summary.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "raw": raw_summary,
        "bounded": bounded_summary,
        "delta": delta,
        "changed_context_count": changed_contexts,
        "top5_preservation_violations": top5_violations,
        "article_recall_improved": len(improved_recall),
        "article_recall_regressed": len(regressed_recall),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
