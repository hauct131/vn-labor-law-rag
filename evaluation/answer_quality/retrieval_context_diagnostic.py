"""Maintained Retrieval and Context Selection Diagnostic CLI.

Executes the live/configured HybridRetriever and records candidate pool (top K)
and generation context (top K) rankings along with expected chunk ranks.
Does NOT use expected labels to influence selection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _get_document_identity(payload: dict[str, Any]) -> str:
    for key in ("document_id", "source_document_id", "document_number", "parent_document_id"):
        val = payload.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    ac = payload.get("article_code") or payload.get("codification_code") or ""
    parts = str(ac).split(".")
    return ".".join(parts[:4]) if len(parts) >= 4 else str(ac)


def run_retrieval_diagnostic(
    *,
    dataset_path: Path,
    case_ids: list[str],
    output_file: Path | None = None,
) -> list[dict[str, Any]]:
    with dataset_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    questions = data.get("questions", [])
    if case_ids:
        target_set = set(case_ids)
        questions = [q for q in questions if q.get("id") in target_set]

    if not questions:
        print(f"No matching questions found in dataset for case IDs: {case_ids}", file=sys.stderr)
        return []

    # Import backend retriever
    from backend.app.core.config import settings
    from backend.app.retrieval.hybrid_retriever import (
        create_hybrid_retriever,
        reciprocal_rank_fusion,
        select_generation_context,
    )

    retriever = create_hybrid_retriever(settings)
    candidate_k = getattr(settings, "retrieval_candidate_k", 50)
    context_k = getattr(settings, "generation_context_k", 10)

    results: list[dict[str, Any]] = []

    for q_item in questions:
        cid = q_item["id"]
        qtext = q_item["question"]
        expected_chunks = set(q_item.get("evidence_chunk_ids", []))

        # Check candidate retrieval support
        if hasattr(retriever, "retrieve_candidates"):
            candidate_hits = retriever.retrieve_candidates(
                qtext,
                top_k=context_k,
                candidate_k=candidate_k,
            )
        else:
            # Fallback for un-refactored retriever: run sparse and dense manually for clean candidate pool
            sparse_hits = retriever.sparse_retriever.retrieve(qtext, top_k=candidate_k)
            dense_hits = retriever.dense_retriever.retrieve(qtext, top_k=candidate_k)
            candidate_hits = reciprocal_rank_fusion(
                {"sparse": sparse_hits, "dense": dense_hits},
                top_k=candidate_k,
                rrf_k=retriever.rrf_k,
                weights=retriever.weights,
            )

        context_hits = select_generation_context(
            candidate_hits,
            generation_context_k=context_k,
        )

        candidate_pool_records: list[dict[str, Any]] = []
        for rank, hit in enumerate(candidate_hits, start=1):
            payload = dict(hit.payload or {})
            doc_id = _get_document_identity(payload)
            candidate_pool_records.append({
                "chunk_id": hit.chunk_id,
                "article_code": payload.get("article_code") or payload.get("codification_code"),
                "document_number": payload.get("document_number") or doc_id,
                "document_id": doc_id,
                "sparse_rank": hit.component_ranks.get("sparse"),
                "dense_rank": hit.component_ranks.get("dense"),
                "fused_rank": rank,
                "rrf_score": round(float(hit.score), 6),
                "retrieval_origin": hit.retrieval_origin,
            })

        context_records: list[dict[str, Any]] = []
        for rank, hit in enumerate(context_hits, start=1):
            payload = dict(hit.payload or {})
            doc_id = _get_document_identity(payload)
            context_records.append({
                "chunk_id": hit.chunk_id,
                "article_code": payload.get("article_code") or payload.get("codification_code"),
                "document_number": payload.get("document_number") or doc_id,
                "document_id": doc_id,
                "sparse_rank": hit.component_ranks.get("sparse"),
                "dense_rank": hit.component_ranks.get("dense"),
                "fused_rank": hit.component_ranks.get("fused_rank", hit.rank),
                "final_context_rank": rank,
                "rrf_score": round(float(hit.score), 6),
                "selection_score": round(float(getattr(hit, "selection_score", hit.score)), 6),
                "cluster_key": getattr(hit, "cluster_key", doc_id),
                "cluster_boost": round(float(getattr(hit, "cluster_boost", 1.0)), 4),
            })

        # Calculate expected chunk ranks
        candidate_chunk_map = {r["chunk_id"]: r["fused_rank"] for r in candidate_pool_records}
        context_chunk_map = {r["chunk_id"]: r["final_context_rank"] for r in context_records}

        cand_ranks = [candidate_chunk_map[c] for c in expected_chunks if c in candidate_chunk_map]
        ctx_ranks = [context_chunk_map[c] for c in expected_chunks if c in context_chunk_map]

        expected_cand_rank = min(cand_ranks) if cand_ranks else None
        expected_ctx_rank = min(ctx_ranks) if ctx_ranks else None

        rec = {
            "case_id": cid,
            "question": qtext,
            "candidate_k": candidate_k,
            "context_k": context_k,
            "expected_chunk_ids": list(expected_chunks),
            "expected_chunk_candidate_rank": expected_cand_rank,
            "expected_chunk_context_rank": expected_ctx_rank,
            "candidate_pool": candidate_pool_records,
            "generation_context": context_records,
        }
        results.append(rec)

    if output_file is not None:
        out_path = Path(output_file).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = out_path.with_suffix(".tmp")
        with tmp_file.open("w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp_file.replace(out_path)
        print(f"Wrote retrieval diagnostic results to {out_path}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieval and Context Selection Diagnostic CLI")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/evaluation/answer-quality-v1/multi_llm_reviewed_questions.json"),
        help="Path to dataset JSON",
    )
    parser.add_argument(
        "--case-id",
        dest="case_ids",
        action="append",
        help="Specific case ID to diagnose (can be passed multiple times)",
    )
    parser.add_argument(
        "--cases",
        type=str,
        help="Comma-separated list of case IDs",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        help="Path to output JSON file",
    )

    args = parser.parse_args()

    case_ids: list[str] = args.case_ids or []
    if args.cases:
        case_ids.extend([c.strip() for c in args.cases.split(",") if c.strip()])

    run_retrieval_diagnostic(
        dataset_path=args.dataset,
        case_ids=case_ids,
        output_file=args.output_file,
    )


if __name__ == "__main__":
    main()
