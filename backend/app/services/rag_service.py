"""Điều phối retrieval → context → OpenRouter generation."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from time import perf_counter
from typing import Any, Callable, Mapping

from ..chains.generation_chain import (
    AsyncAnswerGenerator,
    GenerationProviderError,
    create_generation_chain,
)
from ..retrieval.models import LegalRetriever, RetrievalHit
from ..retrieval.retriever_factory import get_retriever
from ..schemas.ask import AskResponse, LegalSource, RetrievalMethod
from .answer_guardrail import (
    AnswerStatus,
    GuardrailValidationError,
    parse_and_validate_guarded_answer,
)
from .legal_citation import build_citation_metadata
from .official_sources import resolved_source_url


INSUFFICIENT_EVIDENCE_ANSWER = (
    "Không đủ căn cứ trong dữ liệu được cung cấp để trả lời chắc chắn."
)
OUT_OF_SCOPE_ANSWER = (
    "Câu hỏi nằm ngoài phạm vi pháp luật lao động mà hệ thống hiện hỗ trợ."
)
GENERATION_FAILED_ANSWER = (
    "Hệ thống tạm thời chưa thể xác nhận câu trả lời từ các nguồn hiện có."
)

# VnCoreNLP is Java-backed. Keeping retrieval on one worker avoids using the
# same cached segmenter concurrently from unrelated request threads.
_RETRIEVAL_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="legal-retrieval",
)


def _method_value(method: RetrievalMethod | str) -> str:
    value = getattr(method, "value", method)
    return str(value).strip().casefold().replace("-", "_")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def source_from_hit(hit: RetrievalHit) -> LegalSource:
    payload = dict(hit.payload)
    citation = build_citation_metadata(payload)
    return LegalSource(
        chunk_id=hit.chunk_id,
        article_code=_optional_text(
            payload.get("article_code") or payload.get("codification_code")
        ),
        article_number=citation.article_number,
        article_title=_optional_text(payload.get("article_title")),
        document_title=citation.document_title,
        document_number=citation.document_number,
        citation_label=citation.label,
        clause_number=_optional_text(payload.get("clause_number")),
        point_labels=_string_list(
            payload.get("point_labels") or payload.get("point")
        ),
        content=hit.content,
        score=float(hit.score),
        rank=int(hit.rank),
        retrieval_origin=hit.retrieval_origin,
        source_type=_optional_text(payload.get("source_type")),
        source_url=resolved_source_url(payload),
        component_ranks={
            str(name): int(rank)
            for name, rank in hit.component_ranks.items()
        },
    )


def sanitize_sources(sources: list[LegalSource]) -> list[LegalSource]:
    """Remove unusable and duplicate evidence while preserving rank order."""
    sanitized: list[LegalSource] = []
    seen_chunk_ids: set[str] = set()
    for source in sources:
        if not source.chunk_id.strip() or not source.content.strip():
            continue
        if not source.citation_label.strip():
            continue
        if source.chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(source.chunk_id)
        sanitized.append(source)
    return [
        source.model_copy(update={"source_id": f"S{index}"})
        for index, source in enumerate(sanitized, 1)
    ]


def render_legal_context(sources: list[LegalSource]) -> str:
    blocks: list[str] = []
    for index, source in enumerate(sources, 1):
        location_parts: list[str] = []
        if source.clause_number:
            location_parts.append(f"Khoản {source.clause_number}")
        if source.point_labels:
            location_parts.append(f"Điểm {', '.join(source.point_labels)}")
        location = "; ".join(location_parts) or "Toàn điều/đơn vị pháp lý"
        blocks.append(
            "\n".join([
                f"[{source.source_id or f'S{index}'}]",
                f"Dẫn chứng: {source.citation_label}",
                f"Mã pháp điển: {source.article_code or 'Không có'}",
                f"Tên điều: {source.article_title or 'Không có'}",
                f"Vị trí: {location}",
                f"Nội dung:\n{source.content.strip()}",
            ])
        )
    return "\n\n".join(blocks)


class RAGService:
    def __init__(
        self,
        *,
        retriever_provider: Callable[[object], LegalRetriever] = get_retriever,
        generator: AsyncAnswerGenerator | None = None,
        generator_factory: Callable[[], AsyncAnswerGenerator] = (
            create_generation_chain
        ),
        top_k: int = 5,
    ) -> None:
        self.retriever_provider = retriever_provider
        self.generator = generator
        self.generator_factory = generator_factory
        self.top_k = top_k

    async def ask(
        self,
        question: str,
        method: RetrievalMethod | str,
    ) -> AskResponse:
        normalized_question = question.strip()
        normalized_method = _method_value(method)
        if normalized_method not in {"sparse", "dense", "hybrid"}:
            raise ValueError(
                "Phương pháp truy hồi không được hỗ trợ; "
                "chỉ chấp nhận sparse, dense hoặc hybrid."
            )

        started = perf_counter()
        retrieval_started = perf_counter()

        def retrieve() -> list[RetrievalHit]:
            retriever = self.retriever_provider(normalized_method)
            return retriever.retrieve(normalized_question, top_k=self.top_k)

        loop = asyncio.get_running_loop()
        hits = await loop.run_in_executor(_RETRIEVAL_EXECUTOR, retrieve)
        retrieval_ms = (perf_counter() - retrieval_started) * 1000
        sources = sanitize_sources([source_from_hit(hit) for hit in hits])

        if not sources:
            total_ms = (perf_counter() - started) * 1000
            return AskResponse(
                answer=INSUFFICIENT_EVIDENCE_ANSWER,
                method=RetrievalMethod(normalized_method),
                sources=[],
                retrieval_ms=round(retrieval_ms, 2),
                generation_ms=0.0,
                total_ms=round(total_ms, 2),
                model=None,
                insufficient_evidence=True,
            )

        context = render_legal_context(sources)
        generation_started = perf_counter()
        generator = self.generator
        if generator is None:
            generator = self.generator_factory()
            self.generator = generator
        try:
            result = await generator.ainvoke({
                "question": normalized_question,
                "context": context,
            })
        except GenerationProviderError:
            generation_ms = (perf_counter() - generation_started) * 1000
            total_ms = (perf_counter() - started) * 1000
            return AskResponse(
                answer=GENERATION_FAILED_ANSWER,
                method=RetrievalMethod(normalized_method),
                sources=[],
                retrieval_ms=round(retrieval_ms, 2),
                generation_ms=round(generation_ms, 2),
                total_ms=round(total_ms, 2),
                model=None,
                insufficient_evidence=True,
                generation_failed=True,
            )

        generation_ms = (perf_counter() - generation_started) * 1000
        total_ms = (perf_counter() - started) * 1000
        source_map = {
            source.source_id: source
            for source in sources
            if source.source_id is not None
        }
        try:
            guarded = parse_and_validate_guarded_answer(
                result.answer,
                source_map,
            )
        except GuardrailValidationError:
            return AskResponse(
                answer=INSUFFICIENT_EVIDENCE_ANSWER,
                method=RetrievalMethod(normalized_method),
                sources=[],
                retrieval_ms=round(retrieval_ms, 2),
                generation_ms=round(generation_ms, 2),
                total_ms=round(total_ms, 2),
                model=result.model,
                insufficient_evidence=True,
            )

        if guarded.status == AnswerStatus.OUT_OF_SCOPE:
            return AskResponse(
                answer=OUT_OF_SCOPE_ANSWER,
                method=RetrievalMethod(normalized_method),
                sources=[],
                retrieval_ms=round(retrieval_ms, 2),
                generation_ms=round(generation_ms, 2),
                total_ms=round(total_ms, 2),
                model=result.model,
                insufficient_evidence=True,
                out_of_scope=True,
            )

        if guarded.status == AnswerStatus.INSUFFICIENT_EVIDENCE:
            return AskResponse(
                answer=INSUFFICIENT_EVIDENCE_ANSWER,
                method=RetrievalMethod(normalized_method),
                sources=[],
                retrieval_ms=round(retrieval_ms, 2),
                generation_ms=round(generation_ms, 2),
                total_ms=round(total_ms, 2),
                model=result.model,
                insufficient_evidence=True,
            )

        cited_sources = [
            source_map[source_id]
            for source_id in guarded.cited_source_ids
        ]
        return AskResponse(
            answer=guarded.answer,
            method=RetrievalMethod(normalized_method),
            sources=cited_sources,
            retrieval_ms=round(retrieval_ms, 2),
            generation_ms=round(generation_ms, 2),
            total_ms=round(total_ms, 2),
            model=result.model,
        )


@lru_cache(maxsize=1)
def get_rag_service() -> RAGService:
    from ..core.config import settings

    return RAGService(top_k=settings.retrieval_top_k)
