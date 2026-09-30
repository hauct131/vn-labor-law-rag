from __future__ import annotations

from .bm25_retriever import BM25ClauseRetriever
from .clause_acceptance import ClauseAcceptanceGate
from .e5_retriever import E5ClauseRetriever
from .hybrid_retriever import HybridClauseRetriever
from .models import ContractSection
from .retriever import ClauseMatch


class ClausePipeline:
    def __init__(self) -> None:
        self.bm25 = BM25ClauseRetriever()
        self.e5 = E5ClauseRetriever()
        self.hybrid = HybridClauseRetriever(
            bm25=self.bm25,
            e5=self.e5,
            rrf_k=60,
            bm25_weight=1.0,
            e5_weight=1.0,
        )
        self.acceptance_gate = ClauseAcceptanceGate(
            bm25=self.bm25,
            e5=self.e5,
        )

    def prepare(self, sections: list[ContractSection], category_keys: list[str]) -> None:
        self.e5.prepare(sections)
        self.e5.prepare_queries(category_keys)

    def find_accepted_match(
        self,
        sections: list[ContractSection],
        category: str,
        direct_excerpt: str,
    ) -> ClauseMatch | None:
        clause_matches = self.hybrid.retrieve(
            sections=sections,
            category=category,
            top_k=3,
        )
        for candidate in clause_matches:
            acceptance = self.acceptance_gate.evaluate(
                sections=sections,
                category=category,
                candidate=candidate,
                direct_excerpt=direct_excerpt,
            )
            if acceptance.accepted:
                return candidate
        return None
