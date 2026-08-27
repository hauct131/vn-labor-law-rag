"""Điều phối retrieval → context → OpenRouter generation."""

from __future__ import annotations

import asyncio
import logging
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


from ..retrieval.models import (
    CandidatePoolRetriever,
    LegalRetriever,
    RetrievalHit,
)
from ..retrieval.hybrid_retriever import select_generation_context

logger = logging.getLogger(__name__)

from ..core.config import settings

RETRIEVAL_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="legal-retrieval",
)

RETRYABLE_FALLBACK_REASONS = {
    "provider_timeout",
    "provider_http_429",
    "provider_http_5xx",
    "provider_empty_content",
    "provider_invalid_response",
    "generation_parse_error",
    "citation_parse_error",
    "citation_guardrail_rejected",
    "provider_finish_reason_length",
}


class RAGService:
    def __init__(
        self,
        *,
        retriever_provider: Callable[[object], LegalRetriever] = get_retriever,
        generator: AsyncAnswerGenerator | None = None,
        generator_factory: Callable[[], AsyncAnswerGenerator] = (
            create_generation_chain
        ),
        top_k: int = 10,
        candidate_k: int = 50,
    ) -> None:
        self.retriever_provider = retriever_provider
        self.generator = generator
        self.generator_factory = generator_factory
        self.top_k = top_k
        self.candidate_k = candidate_k

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
            if isinstance(retriever, CandidatePoolRetriever):
                candidate_pool = retriever.retrieve_candidates(
                    normalized_question,
                    top_k=self.top_k,
                    candidate_k=self.candidate_k,
                )
                return select_generation_context(
                    candidate_pool,
                    generation_context_k=self.top_k,
                )
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
                fallback_reason="retrieval_context_empty",
            )

        context = render_legal_context(sources)
        generation_started = perf_counter()
        generator = self.generator
        if generator is None:
            generator = self.generator_factory()
            self.generator = generator

        source_map = {
            source.source_id: source
            for source in sources
            if source.source_id is not None
        }

        max_attempts = 2
        result = None
        guarded = None
        fallback_reason: str | None = None
        total_deadline = started + 120.0
        max_tokens_override: int | None = None
        prompt_suffix: str | None = None
        attempt_records: list[dict[str, Any]] = []

        for attempt in range(1, max_attempts + 1):
            remaining = total_deadline - perf_counter()
            if remaining <= 0:
                fallback_reason = "provider_timeout"
                attempt_records.append({
                    "attempt": attempt,
                    "max_tokens": max_tokens_override or getattr(settings, "openrouter_max_tokens", 1200),
                    "remaining_deadline_sec": round(remaining, 2),
                    "fallback_reason": "provider_timeout",
                    "success": False,
                })
                break
            try:
                values = {
                    "question": normalized_question,
                    "context": context,
                }
                invoke_kwargs: dict[str, Any] = {}
                if attempt > 1:
                    invoke_kwargs["attempt"] = attempt
                    invoke_kwargs["timeout_seconds_override"] = remaining
                if max_tokens_override is not None:
                    invoke_kwargs["max_tokens_override"] = max_tokens_override
                if prompt_suffix:
                    invoke_kwargs["prompt_suffix"] = prompt_suffix

                if invoke_kwargs:
                    try:
                        result = await generator.ainvoke(values, **invoke_kwargs)
                    except TypeError:
                        result = await generator.ainvoke(values)
                else:
                    result = await generator.ainvoke(values)

                guarded = parse_and_validate_guarded_answer(
                    result.answer,
                    source_map,
                )
                fallback_reason = None
                attempt_records.append({
                    "attempt": attempt,
                    "max_tokens": max_tokens_override or getattr(settings, "openrouter_max_tokens", 1200),
                    "remaining_deadline_sec": round(remaining, 2),
                    "finish_reason": getattr(result, "finish_reason", "stop"),
                    "fallback_reason": None,
                    "success": True,
                })
                break
            except GenerationProviderError as exc:
                reason = getattr(exc, "fallback_reason", "provider_http_5xx")
                upstream_status = getattr(exc, "status_code", None)
                fallback_reason = reason
                remaining_time = total_deadline - perf_counter()
                attempt_records.append({
                    "attempt": attempt,
                    "max_tokens": max_tokens_override or getattr(settings, "openrouter_max_tokens", 1200),
                    "remaining_deadline_sec": round(remaining_time, 2),
                    "upstream_status": upstream_status,
                    "fallback_reason": reason,
                    "success": False,
                })
                if attempt < max_attempts and reason in RETRYABLE_FALLBACK_REASONS and remaining_time > 0.5:
                    if reason == "provider_finish_reason_length":
                        max_tokens_override = getattr(settings, "openrouter_retry_max_tokens", 2000)
                        prompt_suffix = (
                            "Yêu cầu: Hãy trả lời ngắn gọn, cô đọng nhưng bảo toàn đầy đủ nghĩa vụ, điều kiện, thời hạn, ngoại lệ và trích dẫn pháp lý [S1], [S2]..."
                        )
                        logger.warning(
                            "Generation attempt %d truncated by length; retrying attempt %d with max_tokens=%d (remaining_deadline=%.2fs)...",
                            attempt,
                            attempt + 1,
                            max_tokens_override,
                            remaining_time,
                        )
                    else:
                        logger.warning(
                            "Generation attempt %d failed (reason=%s, upstream_status=%s); retrying attempt %d (remaining_deadline=%.2fs)...",
                            attempt,
                            reason,
                            upstream_status,
                            attempt + 1,
                            remaining_time,
                        )
                    sleep_duration = 2.5 if reason == "provider_http_429" else 0.5
                    await asyncio.sleep(min(sleep_duration, max(0.1, remaining_time - 0.1)))
                    continue
                break
            except GuardrailValidationError as exc:
                reason = getattr(exc, "fallback_reason", "citation_guardrail_rejected")
                fallback_reason = reason
                remaining_time = total_deadline - perf_counter()
                if attempt < max_attempts and reason in RETRYABLE_FALLBACK_REASONS and remaining_time > 0.5:
                    logger.warning(
                        "Generation attempt %d failed (reason=%s); retrying attempt %d (remaining_deadline=%.2fs)...",
                        attempt,
                        reason,
                        attempt + 1,
                        remaining_time,
                    )
                    await asyncio.sleep(min(0.5, max(0.1, remaining_time - 0.1)))
                    continue
                break


        generation_ms = (perf_counter() - generation_started) * 1000
        total_ms = (perf_counter() - started) * 1000

        if guarded is None:
            is_provider_fail = fallback_reason in {
                "provider_timeout",
                "provider_http_429",
                "provider_http_5xx",
                "provider_http_4xx",
                "provider_http_error",
                "provider_empty_content",
                "provider_finish_reason_length",
                "provider_invalid_response",
            }

            return AskResponse(
                answer=GENERATION_FAILED_ANSWER if is_provider_fail else INSUFFICIENT_EVIDENCE_ANSWER,
                method=RetrievalMethod(normalized_method),
                sources=[],
                retrieval_ms=round(retrieval_ms, 2),
                generation_ms=round(generation_ms, 2),
                total_ms=round(total_ms, 2),
                model=result.model if result else None,
                insufficient_evidence=True,
                generation_failed=is_provider_fail,
                fallback_reason=fallback_reason,
                attempt=len(attempt_records) or 1,
                attempt_records=attempt_records,
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
                fallback_reason="scope_classifier_out_of_scope",
                attempt=len(attempt_records) or 1,
                attempt_records=attempt_records,
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
                fallback_reason="insufficient_supported_claims",
                attempt=len(attempt_records) or 1,
                attempt_records=attempt_records,
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
            fallback_reason=None,
            attempt=len(attempt_records) or 1,
            attempt_records=attempt_records,
        )



@lru_cache(maxsize=1)
def get_rag_service() -> RAGService:
    from ..core.config import settings

    return RAGService(
        top_k=settings.generation_context_k,
        candidate_k=settings.retrieval_candidate_k,
    )
