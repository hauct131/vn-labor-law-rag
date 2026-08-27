"""Shared value objects and validation helpers for retrieval pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol, runtime_checkable


class RetrievalError(RuntimeError):
    """Base error raised by a retrieval component."""


class RetrievalConfigurationError(RetrievalError):
    """Raised when a retriever is configured inconsistently."""


class RetrievalBackendError(RetrievalError):
    """Raised when an embedding model or retrieval backend fails."""


class LegalRetriever(Protocol):
    """Small synchronous interface shared by all retrieval methods."""

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
    ) -> list["RetrievalHit"]: ...


@runtime_checkable
class CandidatePoolRetriever(Protocol):
    """Retriever supporting separate candidate pool retrieval."""

    def retrieve_candidates(
        self,
        query: str,
        *,
        top_k: int,
        candidate_k: int,
    ) -> list["RetrievalHit"]: ...



@dataclass(frozen=True, slots=True)
class RetrievalHit:
    """One ranked legal chunk returned by a retrieval component."""

    chunk_id: str
    content: str
    score: float
    rank: int
    retrieval_origin: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    component_scores: Mapping[str, float] = field(default_factory=dict)
    component_ranks: Mapping[str, int] = field(default_factory=dict)
    selection_score: float | None = None
    cluster_key: str | None = None
    cluster_boost: float | None = None
    selection_reason: str | None = None

    def reranked(
        self,
        *,
        rank: int,
        score: float | None = None,
        retrieval_origin: str | None = None,
        component_scores: Mapping[str, float] | None = None,
        component_ranks: Mapping[str, int] | None = None,
        selection_score: float | None = None,
        cluster_key: str | None = None,
        cluster_boost: float | None = None,
        selection_reason: str | None = None,
    ) -> "RetrievalHit":
        """Return a copy carrying a new rank and optional fusion metadata."""
        return replace(
            self,
            rank=rank,
            score=self.score if score is None else float(score),
            retrieval_origin=(
                self.retrieval_origin
                if retrieval_origin is None
                else retrieval_origin
            ),
            component_scores=(
                self.component_scores
                if component_scores is None
                else dict(component_scores)
            ),
            component_ranks=(
                self.component_ranks
                if component_ranks is None
                else dict(component_ranks)
            ),
            selection_score=(
                self.selection_score
                if selection_score is None
                else float(selection_score)
            ),
            cluster_key=(
                self.cluster_key
                if cluster_key is None
                else str(cluster_key)
            ),
            cluster_boost=(
                self.cluster_boost
                if cluster_boost is None
                else float(cluster_boost)
            ),
            selection_reason=(
                self.selection_reason
                if selection_reason is None
                else str(selection_reason)
            ),
        )


def validate_query(query: str) -> str:
    """Return a stripped, non-empty query."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    normalized = query.strip()
    if not normalized:
        raise ValueError("query must not be empty")
    return normalized


def resolve_top_k(top_k: int | None, default_top_k: int) -> int:
    """Resolve and validate the result limit used by a retriever."""
    resolved = default_top_k if top_k is None else top_k
    if isinstance(resolved, bool) or not isinstance(resolved, int):
        raise TypeError("top_k must be an integer")
    if resolved <= 0:
        raise ValueError("top_k must be greater than zero")
    return resolved


def hit_from_payload(
    *,
    point_id: object,
    payload: Mapping[str, Any] | None,
    score: float,
    rank: int,
    retrieval_origin: str,
) -> RetrievalHit:
    """Build a validated hit from a Qdrant point payload."""
    copied_payload = dict(payload or {})
    chunk_id = copied_payload.get("chunk_id") or point_id
    content = copied_payload.get("content")
    if not isinstance(chunk_id, (str, int)) or str(chunk_id).strip() == "":
        raise RetrievalBackendError("retrieved point has no chunk_id")
    if not isinstance(content, str) or not content.strip():
        raise RetrievalBackendError(
            f"retrieved point {chunk_id} has empty payload content"
        )
    return RetrievalHit(
        chunk_id=str(chunk_id),
        content=content,
        score=float(score),
        rank=rank,
        retrieval_origin=retrieval_origin,
        payload=copied_payload,
    )
