from __future__ import annotations

import math
from functools import lru_cache
from typing import Iterable

from fastembed import TextEmbedding

from app.core.config import settings
from app.ingestion.index_qdrant import e5_document_text
from app.retrieval.dense_component import e5_query_text

from .models import ContractSection
from .retriever import ClauseMatch


CATEGORY_QUERIES: dict[str, str] = {
    "probation": (
        "quy định thời gian thử việc tiền lương thử việc "
        "kết thúc thử việc"
    ),
    "salary": (
        "quy định tiền lương kỳ hạn trả lương "
        "hình thức trả lương chậm trả lương"
    ),
    "working_time": (
        "quy định thời giờ làm việc bình thường "
        "nghỉ giữa giờ nghỉ hằng tuần làm thêm giờ"
    ),
    "termination": (
        "quy định đơn phương chấm dứt hợp đồng lao động "
        "thời hạn báo trước"
    ),
}


def _section_text(section: ContractSection) -> str:
    parts: list[str] = []

    if section.heading:
        parts.append(section.heading)

    parts.extend(
        block.text
        for block in section.blocks
        if block.text.strip()
    )

    return "\n".join(parts)


def _to_vector(value: object) -> tuple[float, ...]:
    if hasattr(value, "tolist"):
        raw = value.tolist()
    else:
        raw = list(value)  # type: ignore[arg-type]

    return tuple(float(item) for item in raw)


def _cosine(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> float:
    if len(left) != len(right):
        raise ValueError(
            "Embedding dimensions do not match: "
            f"{len(left)} != {len(right)}"
        )

    dot = sum(
        a * b
        for a, b in zip(left, right)
    )

    left_norm = math.sqrt(
        sum(value * value for value in left)
    )

    right_norm = math.sqrt(
        sum(value * value for value in right)
    )

    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0

    return dot / (left_norm * right_norm)


@lru_cache(maxsize=4)
def _embedding_model(
    model_name: str,
    cache_dir: str,
) -> TextEmbedding:
    """Cache the heavyweight FastEmbed model instance.

    Document and query vectors remain scoped to each
    E5ClauseRetriever instance.
    """
    kwargs: dict[str, object] = {
        "model_name": model_name,
    }

    if cache_dir:
        kwargs["cache_dir"] = cache_dir

    return TextEmbedding(**kwargs)


class E5ClauseRetriever:
    """
    Dense clause retrieval using the project's production E5 model.

    Document embeddings are cached separately from online query retrieval.
    """

    def __init__(
        self,
        *,
        model_name: str | None = None,
    ) -> None:
        self.model_name = (
            model_name
            or settings.dense_embedding_model
        )

        cache_dir = (
            settings.fastembed_cache_dir.strip()
            if settings.fastembed_cache_dir
            else ""
        )

        self._model = _embedding_model(
            self.model_name,
            cache_dir,
        )

        self._document_vectors: dict[
            str,
            tuple[float, ...],
        ] = {}

        self._query_vectors: dict[
            str,
            tuple[float, ...],
        ] = {}

    def prepare(
        self,
        sections: Iterable[ContractSection],
    ) -> None:
        """
        Precompute/cache section embeddings.

        Call this before timed retrieval when measuring online latency.
        """
        missing_texts: list[str] = []

        for section in sections:
            text = _section_text(section)

            if text not in self._document_vectors:
                missing_texts.append(text)

        if not missing_texts:
            return

        prepared = [
            e5_document_text(
                text,
                self.model_name,
            )
            for text in missing_texts
        ]

        vectors = list(
            self._model.embed(
                prepared,
                batch_size=settings.embedding_batch_size,
            )
        )

        if len(vectors) != len(missing_texts):
            raise RuntimeError(
                "Unexpected E5 document embedding count"
            )

        for text, vector in zip(
            missing_texts,
            vectors,
        ):
            self._document_vectors[text] = (
                _to_vector(vector)
            )

    def prepare_queries(
        self,
        categories: Iterable[str],
    ) -> None:
        """
        Precompute/cache fixed category query embeddings.

        Useful when measuring warm online retrieval separately
        from query-embedding preparation.
        """
        for category in categories:
            self._query_vector(category)

    def _query_vector(
        self,
        category: str,
    ) -> tuple[float, ...]:
        query = CATEGORY_QUERIES.get(category)

        if query is None:
            raise ValueError(
                "Unsupported contract review category: "
                f"{category!r}"
            )

        cached = self._query_vectors.get(category)

        if cached is not None:
            return cached

        prepared = e5_query_text(
            query,
            self.model_name,
        )

        vectors = list(
            self._model.embed([prepared])
        )

        if len(vectors) != 1:
            raise RuntimeError(
                "Unexpected E5 query embedding count"
            )

        vector = _to_vector(vectors[0])

        self._query_vectors[category] = vector

        return vector

    def retrieve(
        self,
        sections: list[ContractSection],
        category: str,
        top_k: int = 3,
    ) -> list[ClauseMatch]:
        if category not in CATEGORY_QUERIES:
            raise ValueError(
                "Unsupported contract review category: "
                f"{category!r}"
            )

        if top_k <= 0 or not sections:
            return []

        # Safe fallback for callers that forgot explicit preparation.
        self.prepare(sections)

        query_vector = self._query_vector(
            category
        )

        matches: list[ClauseMatch] = []

        for section in sections:
            text = _section_text(section)

            document_vector = (
                self._document_vectors[text]
            )

            score = _cosine(
                query_vector,
                document_vector,
            )

            matches.append(
                ClauseMatch(
                    section=section,
                    score=score,
                )
            )

        matches.sort(
            key=lambda match: (
                -match.score,
                match.section.index,
            )
        )

        return matches[:top_k]
