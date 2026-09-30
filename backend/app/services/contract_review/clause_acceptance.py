from __future__ import annotations

from dataclasses import dataclass

from .bm25_retriever import BM25ClauseRetriever
from .e5_retriever import CATEGORY_QUERIES, E5ClauseRetriever
from .models import ContractSection
from .retriever import ClauseMatch
from .text_utils import _is_probation_only_salary


# Runtime V2 acceptance calibration.
#
# This is NOT part of the frozen clause-retrieval V1 benchmark.
#
# Absolute E5 cosine scores cannot be used as confidence thresholds:
# relevant and irrelevant scores overlap on the dev set.
#
# For semantic-only candidates we instead require the requested
# category to be meaningfully more compatible with the candidate
# than every competing review category.
DEFAULT_SEMANTIC_MARGIN = 0.01


@dataclass(frozen=True)
class ClauseAcceptanceResult:
    accepted: bool
    reason: str
    bm25_supported: bool
    requested_e5_score: float | None = None
    competing_e5_score: float | None = None
    semantic_margin: float | None = None


class ClauseAcceptanceGate:
    """
    Decide whether a retrieved contract section contains enough direct
    topical evidence to be used by the downstream legal validator.

    Retrieval and acceptance are intentionally separate:

        HybridClauseRetriever
            -> ranks possible sections

        ClauseAcceptanceGate
            -> decides whether a candidate should actually be treated
               as evidence for the requested review category

    The gate does NOT determine legal compliance or severity.
    """

    def __init__(
        self,
        *,
        bm25: BM25ClauseRetriever,
        e5: E5ClauseRetriever,
        semantic_margin: float = DEFAULT_SEMANTIC_MARGIN,
    ) -> None:
        if semantic_margin < 0:
            raise ValueError(
                "semantic_margin must be >= 0"
            )

        self.bm25 = bm25
        self.e5 = e5
        self.semantic_margin = semantic_margin

    def evaluate(
        self,
        *,
        sections: list[ContractSection],
        category: str,
        candidate: ClauseMatch,
        direct_excerpt: str = "",
    ) -> ClauseAcceptanceResult:
        if category not in CATEGORY_QUERIES:
            raise ValueError(
                "Unsupported contract review category: "
                f"{category!r}"
            )

        if not sections:
            return ClauseAcceptanceResult(
                accepted=False,
                reason="no_sections",
                bm25_supported=False,
            )

        candidate_index = candidate.section.index

        if category == "salary" and _is_probation_only_salary(candidate.section.text):
            return ClauseAcceptanceResult(
                accepted=False,
                reason="probation_only_salary",
                bm25_supported=False,
            )

        # --------------------------------------------------
        # 1. Existing deterministic direct-topic evidence
        # --------------------------------------------------
        #
        # Runtime V1 already has a conservative category-specific selector.
        # Reuse its positive evidence as an acceptance signal, without letting
        # it choose the final clause: Hybrid still controls candidate ranking.
        #
        # This is especially useful for concise factual clauses such as:
        # "Lương 12.000.000 đồng ..., trả vào ngày 10 ..." where the
        # phrase-aware BM25 vocabulary may not contain the exact surface form.
        if direct_excerpt.strip():
            direct_normalized = self._normalize_text(
                direct_excerpt
            )
            candidate_normalized = self._normalize_text(
                candidate.section.text
            )

            if (
                direct_normalized
                and candidate_normalized
                and (
                    direct_normalized in candidate_normalized
                    or candidate_normalized in direct_normalized
                )
            ):
                return ClauseAcceptanceResult(
                    accepted=True,
                    reason="direct_topic_evidence",
                    bm25_supported=False,
                )

        # --------------------------------------------------
        # 2. Strong lexical evidence
        # --------------------------------------------------
        bm25_matches = self.bm25.retrieve(
            sections=sections,
            category=category,
            top_k=len(sections),
        )

        bm25_supported = any(
            match.section.index == candidate_index
            for match in bm25_matches
        )

        if bm25_supported:
            return ClauseAcceptanceResult(
                accepted=True,
                reason="bm25_supported",
                bm25_supported=True,
            )

        # --------------------------------------------------
        # 3. Semantic-only evidence
        #
        # Dense E5 always returns a ranking, including for documents
        # that contain no relevant clause. Therefore an absolute E5
        # score is not treated as confidence.
        #
        # Instead, compare how well this SAME candidate section fits
        # the requested category versus the other fixed categories.
        # --------------------------------------------------
        category_scores: dict[str, float] = {}

        for compared_category in CATEGORY_QUERIES:
            matches = self.e5.retrieve(
                sections=sections,
                category=compared_category,
                top_k=len(sections),
            )

            score = self._score_for_section(
                matches=matches,
                section_index=candidate_index,
            )

            if score is not None:
                category_scores[compared_category] = score

        requested_score = category_scores.get(category)

        if requested_score is None:
            return ClauseAcceptanceResult(
                accepted=False,
                reason="missing_requested_e5_score",
                bm25_supported=False,
            )

        competing_scores = [
            score
            for compared_category, score in category_scores.items()
            if compared_category != category
        ]

        if not competing_scores:
            return ClauseAcceptanceResult(
                accepted=False,
                reason="missing_competing_e5_scores",
                bm25_supported=False,
                requested_e5_score=requested_score,
            )

        competing_score = max(competing_scores)
        margin = requested_score - competing_score

        accepted = margin >= self.semantic_margin

        return ClauseAcceptanceResult(
            accepted=accepted,
            reason=(
                "semantic_category_margin"
                if accepted
                else "semantic_category_ambiguous"
            ),
            bm25_supported=False,
            requested_e5_score=requested_score,
            competing_e5_score=competing_score,
            semantic_margin=margin,
        )


    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(
            value.casefold().split()
        )

    @staticmethod
    def _score_for_section(
        *,
        matches: list[ClauseMatch],
        section_index: int,
    ) -> float | None:
        for match in matches:
            if match.section.index == section_index:
                return float(match.score)

        return None
