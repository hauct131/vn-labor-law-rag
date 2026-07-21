"""Create and cache retrievers selected by the public retrieval method."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from .dense_component import create_dense_retriever
from .hybrid_retriever import create_hybrid_retriever
from .models import LegalRetriever
from .sparse_retriever import create_sparse_retriever


def _method_value(method: object) -> str:
    raw_value = getattr(method, "value", method)
    if not isinstance(raw_value, str):
        raise TypeError("retrieval method must be a string or string enum")
    return raw_value.strip().casefold().replace("-", "_")


@lru_cache(maxsize=1)
def _default_dense_retriever() -> LegalRetriever:
    return create_dense_retriever()


@lru_cache(maxsize=1)
def _default_sparse_retriever() -> LegalRetriever:
    return create_sparse_retriever()


@lru_cache(maxsize=1)
def _default_hybrid_retriever() -> LegalRetriever:
    return create_hybrid_retriever(
        sparse_retriever=_default_sparse_retriever(),
        dense_retriever=_default_dense_retriever(),
    )


def clear_retriever_cache() -> None:
    """Clear process-level retrievers, mainly for tests or config reloads."""
    _default_hybrid_retriever.cache_clear()
    _default_sparse_retriever.cache_clear()
    _default_dense_retriever.cache_clear()


def get_retriever(
    method: object,
    *,
    settings_obj: Any | None = None,
    sparse_retriever: LegalRetriever | None = None,
    dense_retriever: LegalRetriever | None = None,
) -> LegalRetriever:
    """Return a configured retriever for one supported method."""
    normalized = _method_value(method)
    use_defaults = (
        settings_obj is None
        and sparse_retriever is None
        and dense_retriever is None
    )

    if normalized == "dense":
        if dense_retriever is not None:
            return dense_retriever
        return (
            _default_dense_retriever()
            if use_defaults
            else create_dense_retriever(settings_obj)
        )

    if normalized == "sparse":
        if sparse_retriever is not None:
            return sparse_retriever
        return (
            _default_sparse_retriever()
            if use_defaults
            else create_sparse_retriever(settings_obj)
        )

    if normalized == "hybrid":
        if use_defaults:
            return _default_hybrid_retriever()
        sparse = sparse_retriever or create_sparse_retriever(settings_obj)
        dense = dense_retriever or create_dense_retriever(settings_obj)
        return create_hybrid_retriever(
            settings_obj,
            sparse_retriever=sparse,
            dense_retriever=dense,
        )

    if normalized in {"graph", "graph_enhanced"}:
        raise NotImplementedError(
            "graph_enhanced retrieval is the next MVP stage; "
            "sparse, dense, and hybrid are available now"
        )
    raise ValueError(
        "unsupported retrieval method "
        f"{normalized!r}; expected sparse, dense, hybrid, or graph_enhanced"
    )
