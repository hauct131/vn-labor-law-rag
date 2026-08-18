"""Hybrid retrieval using BM25–VnCoreNLP, dense E5, and weighted RRF."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .dense_component import create_dense_retriever
from .models import (
    LegalRetriever,
    RetrievalConfigurationError,
    RetrievalHit,
    resolve_top_k,
    validate_query,
)
from .sparse_retriever import create_sparse_retriever


HYBRID_ORIGIN = "hybrid_rrf"


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[RetrievalHit]],
    *,
    top_k: int,
    rrf_k: int = 60,
    weights: Mapping[str, float] | None = None,
) -> list[RetrievalHit]:
    """Fuse independent rankings without mixing incomparable raw scores."""
    limit = resolve_top_k(top_k, top_k)
    if rrf_k < 0:
        raise ValueError("rrf_k must be zero or greater")
    if not rankings:
        return []

    resolved_weights = dict(weights or {})
    totals: dict[str, float] = {}
    representatives: dict[str, RetrievalHit] = {}
    component_scores: dict[str, dict[str, float]] = {}
    component_ranks: dict[str, dict[str, int]] = {}

    for component, hits in rankings.items():
        weight = float(resolved_weights.get(component, 1.0))
        if weight <= 0:
            raise ValueError(
                f"RRF weight for {component!r} must be greater than zero"
            )
        seen_in_component: set[str] = set()
        for rank, hit in enumerate(hits, 1):
            if hit.chunk_id in seen_in_component:
                continue
            seen_in_component.add(hit.chunk_id)
            representatives.setdefault(hit.chunk_id, hit)
            totals[hit.chunk_id] = (
                totals.get(hit.chunk_id, 0.0)
                + weight / (rrf_k + rank)
            )
            component_scores.setdefault(hit.chunk_id, {})[component] = (
                hit.score
            )
            component_ranks.setdefault(hit.chunk_id, {})[component] = rank

    ordered_ids = sorted(
        totals,
        key=lambda chunk_id: (
            -totals[chunk_id],
            min(component_ranks[chunk_id].values()),
            chunk_id,
        ),
    )[:limit]
    return [
        representatives[chunk_id].reranked(
            rank=rank,
            score=totals[chunk_id],
            retrieval_origin=HYBRID_ORIGIN,
            component_scores=component_scores[chunk_id],
            component_ranks=component_ranks[chunk_id],
        )
        for rank, chunk_id in enumerate(ordered_ids, 1)
    ]


def get_stable_document_key(payload: Mapping[str, Any] | None) -> str:
    """Extract stable document key prioritizing explicit IDs over article code parsing."""
    p = dict(payload or {})
    for key in ("document_id", "source_document_id", "document_number", "parent_document_id"):
        val = p.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()

    ac = p.get("article_code") or p.get("codification_code") or ""
    ac_str = str(ac).strip()
    if not ac_str:
        return "unknown_doc"

    parts = ac_str.split(".")
    if len(parts) > 1 and parts[-1].isdigit():
        return ".".join(parts[:-1])
    return ac_str


def select_generation_context(
    hits: Sequence[RetrievalHit],
    *,
    generation_context_k: int,
) -> list[RetrievalHit]:
    """Select the top generation_context_k hits from the fused candidate pool using bounded metadata reranking.

    Rule 3: Preserve fused ranks 1..5 exactly. Only slots 6..10 may be reranked.
    Rule 4: Bounded boost: cluster_boost = min(1.0 + 0.05 * consensus_units, 1.25).
            selection_score = rrf_score * cluster_boost.
    """
    if not hits:
        return []
    limit = max(1, generation_context_k)
    if len(hits) <= limit:
        return [
            hit.reranked(
                rank=idx,
                component_ranks={**hit.component_ranks, "fused_rank": hit.rank},
                selection_score=hit.score,
                cluster_key=get_stable_document_key(hit.payload),
                cluster_boost=1.0,
                selection_reason="pass_through",
            )
            for idx, hit in enumerate(hits, 1)
        ]

    # Mandatory Rule 3: Preserve fused ranks 1..5 exactly
    top5_count = min(5, limit)
    selected_hits: list[RetrievalHit] = []
    seen_chunk_ids: set[str] = set()

    for fused_rank, hit in enumerate(hits[:top5_count], start=1):
        seen_chunk_ids.add(hit.chunk_id)
        doc_key = get_stable_document_key(hit.payload)
        selected_hits.append(
            hit.reranked(
                rank=fused_rank,
                component_ranks={**hit.component_ranks, "fused_rank": fused_rank},
                selection_score=hit.score,
                cluster_key=doc_key,
                cluster_boost=1.0,
                selection_reason="top5_fused_preserved",
            )
        )

    if len(selected_hits) >= limit:
        return selected_hits

    # Slots 6..limit fill via bounded reranking from remaining candidates (hits[5:])
    # Count document key frequency among top 10 fused hits for consensus units
    doc_counts: dict[str, int] = {}
    for hit in hits[:10]:
        doc_key = get_stable_document_key(hit.payload)
        doc_counts[doc_key] = doc_counts.get(doc_key, 0) + 1

    remaining_candidates: list[tuple[float, int, str, RetrievalHit, str, float]] = []
    for fused_rank, hit in enumerate(hits[top5_count:], start=top5_count + 1):
        if hit.chunk_id in seen_chunk_ids:
            continue
        doc_key = get_stable_document_key(hit.payload)
        consensus_units = doc_counts.get(doc_key, 0)
        # Mandatory Rule 4: cluster_boost = min(1.0 + 0.05 * consensus_units, 1.25)
        cluster_boost = min(1.0 + 0.05 * consensus_units, 1.25)
        selection_score = float(hit.score) * cluster_boost
        remaining_candidates.append(
            (selection_score, fused_rank, hit.chunk_id, hit, doc_key, cluster_boost)
        )

    # Sort remaining candidates by (selection_score desc, fused_rank asc, chunk_id asc)
    remaining_candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    for sel_score, fused_rank, chunk_id, hit, doc_key, boost in remaining_candidates:
        if chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk_id)
        final_rank = len(selected_hits) + 1
        selected_hits.append(
            hit.reranked(
                rank=final_rank,
                component_ranks={**hit.component_ranks, "fused_rank": fused_rank},
                selection_score=sel_score,
                cluster_key=doc_key,
                cluster_boost=boost,
                selection_reason="bounded_rerank_slot",
            )
        )
        if len(selected_hits) >= limit:
            break

    return selected_hits


class HybridRetriever:
    """Retrieve candidates from sparse and dense pipelines, then fuse them."""

    def __init__(
        self,
        *,
        sparse_retriever: LegalRetriever,
        dense_retriever: LegalRetriever,
        default_top_k: int = 10,
        candidate_k: int = 50,
        rrf_k: int = 60,
        sparse_weight: float = 1.0,
        dense_weight: float = 1.0,
    ) -> None:
        resolve_top_k(None, default_top_k)
        resolve_top_k(candidate_k, candidate_k)
        if candidate_k < default_top_k:
            raise RetrievalConfigurationError(
                "candidate_k must be greater than or equal to default_top_k"
            )
        if rrf_k < 0:
            raise RetrievalConfigurationError(
                "rrf_k must be zero or greater"
            )
        if sparse_weight <= 0 or dense_weight <= 0:
            raise RetrievalConfigurationError(
                "hybrid component weights must be greater than zero"
            )
        self.sparse_retriever = sparse_retriever
        self.dense_retriever = dense_retriever
        self.default_top_k = default_top_k
        self.candidate_k = candidate_k
        self.rrf_k = rrf_k
        self.weights = {
            "sparse": float(sparse_weight),
            "dense": float(dense_weight),
        }

    def retrieve_candidates(
        self,
        query: str,
        *,
        candidate_k: int | None = None,
        top_k: int | None = None,
    ) -> list[RetrievalHit]:
        """Return the raw weighted-RRF candidate pool top candidate_k hits without invoking select_generation_context."""
        normalized_query = validate_query(query)
        effective_candidate_k = resolve_top_k(candidate_k or top_k, self.candidate_k)
        sparse_hits = self.sparse_retriever.retrieve(
            normalized_query,
            top_k=effective_candidate_k,
        )
        dense_hits = self.dense_retriever.retrieve(
            normalized_query,
            top_k=effective_candidate_k,
        )
        return reciprocal_rank_fusion(
            {
                "sparse": sparse_hits,
                "dense": dense_hits,
            },
            top_k=effective_candidate_k,
            rrf_k=self.rrf_k,
            weights=self.weights,
        )

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        candidate_k: int | None = None,
    ) -> list[RetrievalHit]:
        limit = resolve_top_k(top_k, self.default_top_k)
        effective_candidate_k = resolve_top_k(candidate_k, self.candidate_k)
        if effective_candidate_k < limit:
            raise RetrievalConfigurationError(
                "candidate_k must be greater than or equal to top_k"
            )
        candidate_pool = self.retrieve_candidates(
            query,
            candidate_k=effective_candidate_k,
        )
        return select_generation_context(
            candidate_pool,
            generation_context_k=limit,
        )



def create_hybrid_retriever(
    settings_obj: Any | None = None,
    *,
    sparse_retriever: LegalRetriever | None = None,
    dense_retriever: LegalRetriever | None = None,
    **overrides: Any,
) -> HybridRetriever:
    """Create a shared sparse+dense RRF retriever from application settings."""
    if settings_obj is None:
        from ..core.config import settings as settings_obj

    sparse = sparse_retriever or create_sparse_retriever(settings_obj)
    dense = dense_retriever or create_dense_retriever(settings_obj)
    kwargs: dict[str, Any] = {
        "sparse_retriever": sparse,
        "dense_retriever": dense,
        "default_top_k": getattr(
            settings_obj,
            "generation_context_k",
            getattr(settings_obj, "retrieval_top_k", 10),
        ),
        "candidate_k": getattr(
            settings_obj,
            "retrieval_candidate_k",
            50,
        ),
        "rrf_k": getattr(settings_obj, "hybrid_rrf_k", 60),
        "sparse_weight": getattr(
            settings_obj,
            "hybrid_sparse_weight",
            1.0,
        ),
        "dense_weight": getattr(
            settings_obj,
            "hybrid_dense_weight",
            1.0,
        ),
    }
    kwargs.update(overrides)
    return HybridRetriever(**kwargs)
