"""In-memory BM25 retrieval with Vietnamese tokens from VnCoreNLP.

The production Qdrant collection keeps its original ``Qdrant/bm25`` sparse
vector as a baseline.  The selected Vietnamese sparse pipeline intentionally
uses a separate in-memory index so it never queries that vector with an
incompatible VnCoreNLP vocabulary. At 833 legal chunks the index is small,
fast to search, and can be shared by all requests in one API process.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.paths import resolve_project_path

from .models import (
    RetrievalConfigurationError,
    RetrievalHit,
    resolve_top_k,
    validate_query,
)
from .vncorenlp_bm25 import VnCoreNlpBm25, WordSegmenter


SPARSE_ORIGIN = "sparse_vncorenlp_bm25"


def compute_sha256(path: Path) -> str:
    """Return the SHA-256 fingerprint of one corpus file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def load_legal_chunks(
    path: str | Path,
    *,
    expected_chunks: int | None = None,
    expected_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Load and validate the JSONL corpus used by local sparse retrieval."""
    resolved = resolve_project_path(path)
    if not resolved.is_file():
        raise RetrievalConfigurationError(
            f"legal chunk corpus not found: {resolved}"
        )

    actual_sha256 = compute_sha256(resolved)
    if expected_sha256 and (
        actual_sha256.casefold() != expected_sha256.casefold()
    ):
        raise RetrievalConfigurationError(
            "legal chunk corpus SHA-256 mismatch: expected "
            f"{expected_sha256}, got {actual_sha256}"
        )

    chunks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with resolved.open(encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, 1):
            if not raw_line.strip():
                continue
            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise RetrievalConfigurationError(
                    f"invalid JSON at {resolved}:{line_number}: {exc}"
                ) from exc
            if not isinstance(item, dict):
                raise RetrievalConfigurationError(
                    f"chunk at {resolved}:{line_number} must be an object"
                )
            chunk_id = item.get("chunk_id")
            content = item.get("content")
            if not isinstance(chunk_id, str) or not chunk_id.strip():
                raise RetrievalConfigurationError(
                    f"chunk at {resolved}:{line_number} has no chunk_id"
                )
            if chunk_id in seen_ids:
                raise RetrievalConfigurationError(
                    f"duplicate chunk_id in corpus: {chunk_id}"
                )
            if not isinstance(content, str) or not content.strip():
                raise RetrievalConfigurationError(
                    f"chunk {chunk_id} has empty content"
                )
            seen_ids.add(chunk_id)
            chunks.append(dict(item))

    if not chunks:
        raise RetrievalConfigurationError(
            f"legal chunk corpus is empty: {resolved}"
        )
    if expected_chunks is not None and len(chunks) != expected_chunks:
        raise RetrievalConfigurationError(
            "legal chunk count mismatch: expected "
            f"{expected_chunks}, got {len(chunks)}"
        )
    return chunks, actual_sha256


class VnCoreNlpBm25Retriever:
    """Search a fixed legal corpus with BM25 and VnCoreNLP word tokens."""

    def __init__(
        self,
        *,
        chunks: Sequence[Mapping[str, Any]],
        encoder: VnCoreNlpBm25,
        default_top_k: int = 5,
        corpus_sha256: str | None = None,
    ) -> None:
        if not chunks:
            raise RetrievalConfigurationError("chunks must not be empty")
        resolve_top_k(None, default_top_k)

        copied_chunks: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for position, chunk in enumerate(chunks, 1):
            copied = dict(chunk)
            chunk_id = copied.get("chunk_id")
            content = copied.get("content")
            if not isinstance(chunk_id, str) or not chunk_id.strip():
                raise RetrievalConfigurationError(
                    f"chunk {position} has no chunk_id"
                )
            if chunk_id in seen_ids:
                raise RetrievalConfigurationError(
                    f"duplicate chunk_id: {chunk_id}"
                )
            if not isinstance(content, str) or not content.strip():
                raise RetrievalConfigurationError(
                    f"chunk {chunk_id} has empty content"
                )
            copied_chunks.append(copied)
            seen_ids.add(chunk_id)

        self.chunks = copied_chunks
        self.encoder = encoder
        self.default_top_k = default_top_k
        self.corpus_sha256 = corpus_sha256
        self._postings: dict[int, list[tuple[int, float]]] = {}
        self._build_postings()

    @classmethod
    def from_chunks(
        cls,
        chunks: Sequence[Mapping[str, Any]],
        *,
        segmenter: WordSegmenter,
        default_top_k: int = 5,
        k: float = 1.2,
        b: float = 0.75,
        corpus_sha256: str | None = None,
    ) -> "VnCoreNlpBm25Retriever":
        documents = [str(chunk.get("content") or "") for chunk in chunks]
        encoder = VnCoreNlpBm25.from_documents(
            documents,
            segmenter=segmenter,
            k=k,
            b=b,
        )
        return cls(
            chunks=chunks,
            encoder=encoder,
            default_top_k=default_top_k,
            corpus_sha256=corpus_sha256,
        )

    @classmethod
    def from_jsonl(
        cls,
        corpus_path: str | Path,
        *,
        model_dir: str | Path,
        default_top_k: int = 5,
        k: float = 1.2,
        b: float = 0.75,
        expected_chunks: int | None = None,
        expected_sha256: str | None = None,
    ) -> "VnCoreNlpBm25Retriever":
        chunks, actual_sha256 = load_legal_chunks(
            corpus_path,
            expected_chunks=expected_chunks,
            expected_sha256=expected_sha256,
        )
        encoder = VnCoreNlpBm25.from_model_dir(
            [chunk["content"] for chunk in chunks],
            resolve_project_path(model_dir),
            k=k,
            b=b,
        )
        return cls(
            chunks=chunks,
            encoder=encoder,
            default_top_k=default_top_k,
            corpus_sha256=actual_sha256,
        )

    def _build_postings(self) -> None:
        for document_index, chunk in enumerate(self.chunks):
            embedding = next(self.encoder.embed([chunk["content"]]))
            for token_id, term_weight in zip(
                embedding.indices,
                embedding.values,
                strict=True,
            ):
                self._postings.setdefault(int(token_id), []).append((
                    document_index,
                    float(term_weight),
                ))

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            **self.encoder.metadata,
            "engine": "in_memory_inverted_index",
            "document_count": len(self.chunks),
            "corpus_sha256": self.corpus_sha256,
            "retrieval_origin": SPARSE_ORIGIN,
        }

    def _idf(self, document_frequency: int) -> float:
        document_count = len(self.chunks)
        return math.log(
            1.0
            + (document_count - document_frequency + 0.5)
            / (document_frequency + 0.5)
        )

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
    ) -> list[RetrievalHit]:
        """Return the highest-scoring chunks for one Vietnamese question."""
        normalized_query = validate_query(query)
        limit = resolve_top_k(top_k, self.default_top_k)
        query_embedding = next(self.encoder.query_embed([normalized_query]))

        scores: dict[int, float] = {}
        for token_id, query_weight in zip(
            query_embedding.indices,
            query_embedding.values,
            strict=True,
        ):
            postings = self._postings.get(int(token_id), [])
            if not postings:
                continue
            idf = self._idf(len(postings))
            for document_index, document_weight in postings:
                scores[document_index] = (
                    scores.get(document_index, 0.0)
                    + float(query_weight) * idf * document_weight
                )

        ranked = sorted(
            scores.items(),
            key=lambda item: (
                -item[1],
                str(self.chunks[item[0]]["chunk_id"]),
            ),
        )[:limit]
        return [
            RetrievalHit(
                chunk_id=str(self.chunks[document_index]["chunk_id"]),
                content=str(self.chunks[document_index]["content"]),
                score=score,
                rank=rank,
                retrieval_origin=SPARSE_ORIGIN,
                payload=dict(self.chunks[document_index]),
            )
            for rank, (document_index, score) in enumerate(ranked, 1)
        ]


def create_sparse_retriever(
    settings_obj: Any | None = None,
    **overrides: Any,
) -> VnCoreNlpBm25Retriever:
    """Create the production BM25–VnCoreNLP retriever from settings."""
    if settings_obj is None:
        from ..core.config import settings as settings_obj

    kwargs: dict[str, Any] = {
        "corpus_path": getattr(
            settings_obj,
            "legal_chunks_path",
            "data/releases/labor-law-2026-07-28-candidate/chunks.jsonl",
        ),
        "model_dir": getattr(
            settings_obj,
            "vncorenlp_model_dir",
            "models/vncorenlp",
        ),
        "default_top_k": getattr(settings_obj, "retrieval_top_k", 5),
        "k": getattr(settings_obj, "bm25_k", 1.2),
        "b": getattr(settings_obj, "bm25_b", 0.75),
        "expected_chunks": getattr(
            settings_obj,
            "retrieval_expected_chunks",
            None,
        ),
        "expected_sha256": getattr(
            settings_obj,
            "retrieval_corpus_sha256",
            None,
        ),
    }
    kwargs.update(overrides)
    return VnCoreNlpBm25Retriever.from_jsonl(**kwargs)
