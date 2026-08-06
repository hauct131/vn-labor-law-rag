"""Public retrieval-core API."""

from .dense_component import DenseRetriever, create_dense_retriever
from .hybrid_retriever import (
    HybridRetriever,
    create_hybrid_retriever,
    reciprocal_rank_fusion,
)
from .models import RetrievalHit
from .retriever_factory import get_retriever
from .sparse_retriever import (
    VnCoreNlpBm25Retriever,
    create_sparse_retriever,
)

__all__ = [
    "DenseRetriever",
    "HybridRetriever",
    "RetrievalHit",
    "VnCoreNlpBm25Retriever",
    "create_dense_retriever",
    "create_hybrid_retriever",
    "create_sparse_retriever",
    "get_retriever",
    "reciprocal_rank_fusion",
]
