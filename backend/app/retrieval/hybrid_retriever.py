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


class HybridRetriever:
    """Retrieve candidates from sparse and dense pipelines, then fuse them."""

    def __init__(
        self,
        *,
        sparse_retriever: LegalRetriever,
        dense_retriever: LegalRetriever,
        default_top_k: int = 5,
        candidate_k: int = 20,
        rrf_k: int = 60,
        sparse_weight: float = 1.0,
        dense_weight: float = 1.0,
    ) -> None:
        resolve_top_k(None, default_top_k)
        resolve_top_k(candidate_k, candidate_k)
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

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
    ) -> list[RetrievalHit]:
        normalized_query = validate_query(query)
        limit = resolve_top_k(top_k, self.default_top_k)
        candidate_limit = max(limit, self.candidate_k)
        sparse_hits = self.sparse_retriever.retrieve(
            normalized_query,
            top_k=candidate_limit,
        )
        dense_hits = self.dense_retriever.retrieve(
            normalized_query,
            top_k=candidate_limit,
        )
        return reciprocal_rank_fusion(
            {
                "sparse": sparse_hits,
                "dense": dense_hits,
            },
            top_k=limit,
            rrf_k=self.rrf_k,
            weights=self.weights,
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
        "default_top_k": getattr(settings_obj, "retrieval_top_k", 5),
        "candidate_k": getattr(
            settings_obj,
            "retrieval_candidate_k",
            20,
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
