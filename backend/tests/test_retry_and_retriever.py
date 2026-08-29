"""Unit tests for CandidatePoolRetriever protocol, backend retry policy, bounded reranking, and stable document keys."""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from backend.app.retrieval.models import CandidatePoolRetriever, LegalRetriever, RetrievalHit
from backend.app.retrieval.hybrid_retriever import HybridRetriever, select_generation_context, get_stable_document_key
from backend.app.services.rag_service import RAGService, RETRYABLE_FALLBACK_REASONS
from backend.app.chains.generation_chain import GenerationProviderError, GenerationResult
from backend.app.services.answer_guardrail import GuardrailValidationError


def test_candidate_pool_retriever_protocol():
    """Verify HybridRetriever implements CandidatePoolRetriever protocol."""
    sparse = MagicMock(spec=LegalRetriever)
    dense = MagicMock(spec=LegalRetriever)
    sparse.retrieve.return_value = []
    dense.retrieve.return_value = []

    retriever = HybridRetriever(
        sparse_retriever=sparse,
        dense_retriever=dense,
        default_top_k=10,
        candidate_k=50,
    )

    assert isinstance(retriever, CandidatePoolRetriever)
    assert hasattr(retriever, "retrieve_candidates")


def test_stable_document_key_normalization():
    """Verify get_stable_document_key prioritizes explicit IDs and handles normalized fallback patterns."""
    # Explicit document ID / number
    assert get_stable_document_key({"document_id": "DOC123", "article_code": "20.2.NĐ.128.7"}) == "DOC123"
    assert get_stable_document_key({"document_number": "128/2020/NĐ-CP", "article_code": "NĐ128.7"}) == "128/2020/NĐ-CP"

    # Normalized article code fallback patterns
    assert get_stable_document_key({"article_code": "NĐ128.7"}) == "NĐ128"
    assert get_stable_document_key({"article_code": "NĐ129.75"}) == "NĐ129"
    assert get_stable_document_key({"article_code": "20.2.LQ.48"}) == "20.2.LQ"
    assert get_stable_document_key({"article_code": "20.2.NĐ.3.97"}) == "20.2.NĐ.3"
    assert get_stable_document_key({"article_code": "20.2.TT.3.5"}) == "20.2.TT.3"


def test_bounded_reranking_preserves_top5_and_caps_boost():
    """Verify bounded reranking preserves top 5 fused candidates exactly and caps cluster boost at 1.25."""
    hits = [
        RetrievalHit(f"fused_{i}", f"content {i}", 1.0 - i * 0.01, i, "rrf", payload={"article_code": f"DOC_POPULAR.{i}"})
        for i in range(1, 15)
    ]

    selected = select_generation_context(hits, generation_context_k=10)
    assert len(selected) == 10

    # Rule 3: Top 5 fused candidates preserved in exact order
    for idx in range(5):
        assert selected[idx].chunk_id == hits[idx].chunk_id
        assert selected[idx].rank == idx + 1
        assert getattr(selected[idx], "selection_reason", "") == "top5_fused_preserved"

    # Rule 4: Slots 6..10 have cluster_boost <= 1.25
    for idx in range(5, 10):
        boost = getattr(selected[idx], "cluster_boost", 1.0)
        assert boost is not None and boost <= 1.25


def test_bounded_reranking_boost_parameters():
    """Verify selected defaults and reject invalid bounded-boost parameters."""
    assert select_generation_context.__kwdefaults__ == {
        "boost_step": 0.025,
        "boost_cap": 1.25,
    }

    with pytest.raises(ValueError, match="boost_step"):
        select_generation_context([], generation_context_k=10, boost_step=-0.001)

    with pytest.raises(ValueError, match="boost_cap"):
        select_generation_context([], generation_context_k=10, boost_cap=0.99)


def test_rag_service_length_retry():
    """Verify RAGService retries on provider_finish_reason_length with higher max_tokens."""
    async def _run():
        hit = RetrievalHit(
            chunk_id="chunk-1",
            content="Nội dung luật lao động",
            score=0.9,
            rank=1,
            retrieval_origin="hybrid_rrf",
        )
        mock_retriever = MagicMock(spec=HybridRetriever)
        mock_retriever.retrieve_candidates.return_value = [hit]

        valid_answer_json = json.dumps({
            "status": "answerable",
            "answer": "Trả lời căn cứ theo [S1].",
            "cited_source_ids": ["S1"],
        }, ensure_ascii=False)

        mock_generator = AsyncMock()
        mock_generator.ainvoke.side_effect = [
            GenerationProviderError("Truncated", fallback_reason="provider_finish_reason_length"),
            GenerationResult(answer=valid_answer_json, model="test-model", attempt=2),
        ]

        service = RAGService(
            retriever_provider=lambda m: mock_retriever,
            generator=mock_generator,
        )

        response = await service.ask("Câu hỏi 057", "hybrid")

        assert mock_generator.ainvoke.call_count == 2
        second_call_kwargs = mock_generator.ainvoke.call_args_list[1].kwargs
        assert second_call_kwargs.get("max_tokens_override") == 2000
        assert second_call_kwargs.get("attempt") == 2
        assert response.generation_failed is False
        assert len(response.sources) == 1

    asyncio.run(_run())


def test_retryable_reasons_set():
    """Verify exact set of retryable fallback reasons."""
    assert "provider_timeout" in RETRYABLE_FALLBACK_REASONS
    assert "provider_http_429" in RETRYABLE_FALLBACK_REASONS
    assert "provider_http_5xx" in RETRYABLE_FALLBACK_REASONS
    assert "provider_empty_content" in RETRYABLE_FALLBACK_REASONS
    assert "provider_invalid_response" in RETRYABLE_FALLBACK_REASONS
    assert "generation_parse_error" in RETRYABLE_FALLBACK_REASONS
    assert "citation_parse_error" in RETRYABLE_FALLBACK_REASONS
    assert "provider_finish_reason_length" in RETRYABLE_FALLBACK_REASONS

    # Non-retryable
    assert "provider_http_4xx" not in RETRYABLE_FALLBACK_REASONS
    assert "scope_classifier_out_of_scope" not in RETRYABLE_FALLBACK_REASONS
    assert "insufficient_supported_claims" not in RETRYABLE_FALLBACK_REASONS
    assert "retrieval_context_empty" not in RETRYABLE_FALLBACK_REASONS
