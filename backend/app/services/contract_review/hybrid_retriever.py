from __future__ import annotations

from .bm25_retriever import BM25ClauseRetriever
from .e5_retriever import E5ClauseRetriever
from .models import ContractSection
from .retriever import ClauseMatch


class HybridClauseRetriever:
    """
    Equal-weight Reciprocal Rank Fusion over BM25 and E5.

    Configuration is intentionally fixed before benchmark evaluation:
    - BM25 weight = 1.0
    - E5 weight = 1.0
    - RRF k = 60

    No score normalization is required because RRF operates on ranks.
    """

    def __init__(
        self,
        *,
        bm25: BM25ClauseRetriever,
        e5: E5ClauseRetriever,
        rrf_k: int = 60,
        bm25_weight: float = 1.0,
        e5_weight: float = 1.0,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be > 0")

        if bm25_weight < 0 or e5_weight < 0:
            raise ValueError(
                "RRF weights must be non-negative"
            )

        if bm25_weight == 0 and e5_weight == 0:
            raise ValueError(
                "At least one RRF weight must be positive"
            )

        self.bm25 = bm25
        self.e5 = e5
        self.rrf_k = rrf_k
        self.bm25_weight = bm25_weight
        self.e5_weight = e5_weight

    def retrieve(
        self,
        sections: list[ContractSection],
        category: str,
        top_k: int = 3,
    ) -> list[ClauseMatch]:
        if top_k <= 0 or not sections:
            return []

        # Contracts contain few sections, so fuse complete rankings
        # rather than introducing another candidate_k hyperparameter.
        component_k = len(sections)

        bm25_matches = self.bm25.retrieve(
            sections=sections,
            category=category,
            top_k=component_k,
        )

        e5_matches = self.e5.retrieve(
            sections=sections,
            category=category,
            top_k=component_k,
        )

        by_index = {
            section.index: section
            for section in sections
        }

        scores: dict[int, float] = {}

        for rank, match in enumerate(
            bm25_matches,
            start=1,
        ):
            scores[match.section.index] = (
                scores.get(match.section.index, 0.0)
                + self.bm25_weight
                / (self.rrf_k + rank)
            )

        for rank, match in enumerate(
            e5_matches,
            start=1,
        ):
            scores[match.section.index] = (
                scores.get(match.section.index, 0.0)
                + self.e5_weight
                / (self.rrf_k + rank)
            )

        matches = [
            ClauseMatch(
                section=by_index[index],
                score=score,
            )
            for index, score in scores.items()
        ]

        matches.sort(
            key=lambda match: (
                -match.score,
                match.section.index,
            )
        )

        return matches[:top_k]
