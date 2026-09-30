from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from functools import lru_cache
from typing import Any

from app.core.config import settings
from app.core.paths import resolve_project_path
from app.schemas.ask import LegalSource
from app.services.legal_citation import build_citation_metadata
from app.services.official_sources import resolved_source_url

from .text_utils import _string_list, _tokens


class CanonicalEvidenceRetriever:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.docs = [Counter(_tokens(str(chunk["content"]))) for chunk in chunks]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.average_length = sum(self.lengths) / max(len(self.lengths), 1)
        self.document_frequency: Counter[str] = Counter()
        for doc in self.docs:
            self.document_frequency.update(doc.keys())

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        preferred_article_codes: tuple[str, ...] = (),
        dedupe_articles: bool = True,
    ) -> list[LegalSource]:
        query_terms = Counter(_tokens(query))
        scored: list[tuple[float, float, int]] = []
        total = len(self.docs)
        for index, doc in enumerate(self.docs):
            score = 0.0
            length = self.lengths[index] or 1
            for term, query_count in query_terms.items():
                frequency = doc.get(term, 0)
                if not frequency:
                    continue
                df = self.document_frequency[term]
                idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * length / self.average_length)
                score += query_count * idf * frequency * 2.2 / denominator
            raw_score = score
            article_code = str(self.chunks[index].get("article_code") or "")
            ranking_score = raw_score
            if article_code in preferred_article_codes:
                ranking_score += 16.0
            if ranking_score > 0:
                scored.append((ranking_score, raw_score, index))
        scored.sort(key=lambda item: (-item[0], str(self.chunks[item[2]]["chunk_id"])))
        result: list[LegalSource] = []
        seen_articles: set[str] = set()
        for _ranking_score, raw_score, index in scored:
            payload = self.chunks[index]
            article_code = str(
                payload.get("article_code") or payload.get("codification_code") or ""
            )
            deduplication_key = article_code or str(payload["chunk_id"])
            if (dedupe_articles and deduplication_key in seen_articles):
                continue
            if dedupe_articles:
                seen_articles.add(deduplication_key)
            rank = len(result) + 1
            citation = build_citation_metadata(payload)
            result.append(LegalSource(
                source_id=f"S{rank}",
                chunk_id=str(payload["chunk_id"]),
                article_code=article_code or None,
                article_number=citation.article_number,
                article_title=str(payload.get("article_title") or "") or None,
                document_title=citation.document_title,
                document_number=citation.document_number,
                citation_label=citation.label,
                clause_number=str(payload.get("clause_number") or "") or None,
                point_labels=_string_list(payload.get("point_labels")),
                content=str(payload["content"]),
                score=round(raw_score, 6),
                rank=rank,
                retrieval_origin="contract_canonical_lexical_v1",
                source_type=str(payload.get("source_type") or "") or None,
                source_url=resolved_source_url(payload),
                component_ranks={"contract_lexical": rank},
            ))
            if len(result) == top_k:
                break
        return result


@lru_cache(maxsize=1)
def evidence_retriever() -> CanonicalEvidenceRetriever:
    path = resolve_project_path(settings.legal_chunks_path)
    digest_builder = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest_builder.update(block)
    digest = digest_builder.hexdigest()
    if digest != settings.retrieval_corpus_sha256:
        raise RuntimeError("Canonical corpus SHA-256 mismatch.")
    chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(chunks) != settings.retrieval_expected_chunks:
        raise RuntimeError("Canonical corpus chunk count mismatch.")
    return CanonicalEvidenceRetriever(chunks)


# CONTRACT_REVIEW_CROSS_ENCODER_V2_OBSERVABILITY:
# Materialize final rerank metadata without changing article/chunk selection.
def _finalize_reranked_sources(items: list[Any]) -> list[LegalSource]:
    sources: list[LegalSource] = []
    for final_rank, item in enumerate(items, start=1):
        source = item.source
        component_ranks = dict(source.component_ranks or {})
        update: dict[str, Any] = {
            "source_id": f"S{final_rank}",
            "rank": final_rank,
            "component_ranks": component_ranks,
        }
        if item.reranker_score is not None:
            update["score"] = round(float(item.reranker_score), 6)
            update["retrieval_origin"] = "contract_cross_encoder_v2"
            component_ranks["contract_cross_encoder"] = final_rank
        sources.append(source.model_copy(update=update))
    return sources
