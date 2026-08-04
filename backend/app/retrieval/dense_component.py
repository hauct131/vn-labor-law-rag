"""Dense E5 retrieval against the production Qdrant collection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import (
    RetrievalBackendError,
    RetrievalConfigurationError,
    RetrievalHit,
    hit_from_payload,
    resolve_top_k,
    validate_query,
)


DENSE_ORIGIN = "dense_e5"


def e5_query_text(query: str, model_name: str) -> str:
    """Apply the asymmetric E5 query prefix without double-prefixing."""
    normalized = validate_query(query)
    if "e5" not in model_name.casefold():
        return normalized
    if normalized.startswith("query: "):
        return normalized
    return f"query: {normalized}"


def _vector_values(embedding: Any) -> list[float]:
    values = (
        embedding.tolist()
        if hasattr(embedding, "tolist")
        else list(embedding)
    )
    return [float(value) for value in values]


class DenseRetriever:
    """Lazily load FastEmbed and query one named dense Qdrant vector."""

    def __init__(
        self,
        *,
        qdrant_url: str,
        collection_name: str,
        api_key: str | None = None,
        model_name: str,
        vector_name: str = "dense",
        vector_size: int = 1024,
        default_top_k: int = 5,
        threads: int = 6,
        cache_dir: str | Path | None = None,
        expected_corpus_sha256: str | None = None,
        client: Any | None = None,
        embedding_model: Any | None = None,
    ) -> None:
        if not qdrant_url.strip():
            raise RetrievalConfigurationError("qdrant_url must not be empty")
        if not collection_name.strip():
            raise RetrievalConfigurationError(
                "collection_name must not be empty"
            )
        if not model_name.strip():
            raise RetrievalConfigurationError("model_name must not be empty")
        if not vector_name.strip():
            raise RetrievalConfigurationError("vector_name must not be empty")
        if vector_size <= 0:
            raise RetrievalConfigurationError(
                "vector_size must be greater than zero"
            )
        if threads <= 0:
            raise RetrievalConfigurationError(
                "threads must be greater than zero"
            )
        resolve_top_k(None, default_top_k)

        self.qdrant_url = qdrant_url
        self.collection_name = collection_name
        self.api_key = api_key.strip() if api_key and api_key.strip() else None
        self.model_name = model_name
        self.vector_name = vector_name
        self.vector_size = vector_size
        self.default_top_k = default_top_k
        self.threads = threads
        self.cache_dir = (
            str(Path(cache_dir).expanduser()) if cache_dir else None
        )
        self.expected_corpus_sha256 = (
            expected_corpus_sha256.casefold()
            if expected_corpus_sha256
            else None
        )
        self._client = client
        self._embedding_model = embedding_model

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from qdrant_client import QdrantClient
            except ImportError as exc:
                raise RetrievalConfigurationError(
                    "qdrant-client is required for dense retrieval"
                ) from exc
            client_kwargs: dict[str, Any] = {"url": self.qdrant_url}
            if self.api_key is not None:
                client_kwargs["api_key"] = self.api_key
            self._client = QdrantClient(**client_kwargs)
        return self._client

    def _get_embedding_model(self) -> Any:
        if self._embedding_model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise RetrievalConfigurationError(
                    "fastembed is required for dense retrieval"
                ) from exc

            kwargs: dict[str, Any] = {
                "model_name": self.model_name,
                "threads": self.threads,
            }
            if self.cache_dir:
                kwargs["cache_dir"] = self.cache_dir
            self._embedding_model = TextEmbedding(**kwargs)
        return self._embedding_model

    def _validate_payload_fingerprint(
        self,
        payload: dict[str, Any],
        chunk_id: str,
    ) -> None:
        if self.expected_corpus_sha256 is None:
            return
        actual = payload.get("_index_corpus_sha256")
        if not isinstance(actual, str) or (
            actual.casefold() != self.expected_corpus_sha256
        ):
            raise RetrievalBackendError(
                "Qdrant payload corpus fingerprint mismatch for "
                f"chunk {chunk_id}: {actual!r}"
            )

    def warmup(self) -> None:
        """Load the model/client before latency-sensitive evaluation."""
        prepared_query = e5_query_text(
            "khởi động bộ truy hồi",
            self.model_name,
        )
        try:
            embedding = next(iter(
                self._get_embedding_model().embed([prepared_query])
            ))
            self._get_client()
        except StopIteration as exc:
            raise RetrievalBackendError(
                "dense model returned no warm-up embedding"
            ) from exc
        except RetrievalConfigurationError:
            raise
        except Exception as exc:
            raise RetrievalBackendError(
                f"dense retriever warm-up failed: {exc}"
            ) from exc

        vector = _vector_values(embedding)
        if len(vector) != self.vector_size:
            raise RetrievalBackendError(
                "dense warm-up vector size mismatch: expected "
                f"{self.vector_size}, got {len(vector)}"
            )

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
    ) -> list[RetrievalHit]:
        """Embed one legal question and return ranked Qdrant payloads."""
        limit = resolve_top_k(top_k, self.default_top_k)
        prepared_query = e5_query_text(query, self.model_name)

        try:
            embedding = next(iter(
                self._get_embedding_model().embed([prepared_query])
            ))
        except StopIteration as exc:
            raise RetrievalBackendError(
                "dense model returned no query embedding"
            ) from exc
        except RetrievalConfigurationError:
            raise
        except Exception as exc:
            raise RetrievalBackendError(
                f"dense query embedding failed: {exc}"
            ) from exc

        vector = _vector_values(embedding)
        if len(vector) != self.vector_size:
            raise RetrievalBackendError(
                "dense query vector size mismatch: expected "
                f"{self.vector_size}, got {len(vector)}"
            )

        try:
            response = self._get_client().query_points(
                collection_name=self.collection_name,
                query=vector,
                using=self.vector_name,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            points = response.points
        except RetrievalConfigurationError:
            raise
        except Exception as exc:
            raise RetrievalBackendError(
                f"Qdrant dense query failed: {exc}"
            ) from exc

        hits: list[RetrievalHit] = []
        for rank, point in enumerate(points, 1):
            hit = hit_from_payload(
                point_id=getattr(point, "id", None),
                payload=getattr(point, "payload", None),
                score=getattr(point, "score", 0.0),
                rank=rank,
                retrieval_origin=DENSE_ORIGIN,
            )
            self._validate_payload_fingerprint(
                dict(hit.payload),
                hit.chunk_id,
            )
            hits.append(hit)
        return hits


def create_dense_retriever(
    settings_obj: Any | None = None,
    **overrides: Any,
) -> DenseRetriever:
    """Create the production dense retriever from application settings."""
    if settings_obj is None:
        from ..core.config import settings as settings_obj

    cache_dir = getattr(settings_obj, "fastembed_cache_dir", None) or None
    kwargs: dict[str, Any] = {
        "qdrant_url": settings_obj.qdrant_url,
        "collection_name": settings_obj.qdrant_collection,
        "api_key": getattr(settings_obj, "qdrant_api_key", None) or None,
        "model_name": settings_obj.dense_embedding_model,
        "vector_name": settings_obj.dense_vector_name,
        "vector_size": settings_obj.dense_vector_size,
        "default_top_k": getattr(settings_obj, "retrieval_top_k", 5),
        "threads": getattr(settings_obj, "embedding_threads", 6),
        "cache_dir": cache_dir,
        "expected_corpus_sha256": getattr(
            settings_obj,
            "retrieval_corpus_sha256",
            None,
        ),
    }
    kwargs.update(overrides)
    return DenseRetriever(**kwargs)
