"""Grounded, deterministic labor contract review orchestration."""

from __future__ import annotations

from app.schemas.ask import RetrievalMethod
from .contract_review_reranker import get_contract_review_reranker
from .contract_review.categories import CATEGORIES
from .contract_review.clause_pipeline import ClausePipeline
from .contract_review.direct_evidence import _excerpt_for
from .contract_review.legal_evidence import (
    CanonicalEvidenceRetriever,
    _finalize_reranked_sources,
    evidence_retriever,
)
from .contract_review.models import CategoryRule, FindingDraft, ReviewDraft
from .contract_review.segmenter import segment_contract
from .contract_review.text_utils import (
    _ascii,
    _has_pay_date,
    _is_contract_section_heading,
    _is_negated_probation_mention,
    _number_near_anchors,
    _paragraphs,
    _plain_text,
    _string_list,
    _tokens,
    _unit_numbers,
)
from .contract_review.validators import _analysis


def build_review_summary(findings: list[FindingDraft]) -> str:
    missing = sum(
        item.evidence_status == "insufficient_evidence" for item in findings
    )
    attention = sum(
        item.evidence_status != "insufficient_evidence"
        and item.severity == "attention"
        for item in findings
    )
    warnings = sum(
        item.evidence_status != "insufficient_evidence"
        and item.severity == "warning"
        for item in findings
    )
    return (
        f"Đã rà soát {len(findings)} nhóm điều khoản. Có {attention} nhóm cần kiểm tra, "
        f"{warnings} nhóm cần ưu tiên kiểm tra và {missing} nhóm chưa tìm thấy nội dung "
        f"thể hiện rõ trong hợp đồng."
    )


# CONTRACT_REVIEW_CROSS_ENCODER_V2_OBSERVABILITY:
# Materialize final rerank metadata (contract_cross_encoder_v2) without changing article/chunk selection.


def review_contract(
    text: str,
    method: RetrievalMethod,
) -> ReviewDraft:
    paragraphs = _paragraphs(text)
    sections = segment_contract(paragraphs)

    # --------------------------------------------------
    # Contract-side clause retrieval
    # --------------------------------------------------
    clause_pipeline = ClausePipeline()
    clause_pipeline.prepare(
        sections,
        [rule.key for rule in CATEGORIES],
    )

    # --------------------------------------------------
    # Legal evidence retrieval
    # --------------------------------------------------
    retriever = evidence_retriever()
    findings: list[FindingDraft] = []
    for rule in CATEGORIES:
        direct_excerpt = _excerpt_for(
            rule,
            paragraphs,
        )
        accepted_match = clause_pipeline.find_accepted_match(
            sections=sections,
            category=rule.key,
            direct_excerpt=direct_excerpt,
        )
        excerpt = (
            accepted_match.section.text
            if accepted_match is not None
            else ""
        )
        query = rule.query + (
            f" Nội dung hợp đồng: {excerpt[:500]}"
            if excerpt
            else ""
        )
        # CONTRACT_REVIEW_CROSS_ENCODER_V2:
        # Preserve the legal-retrieval/reranking pipeline.
        reranker = get_contract_review_reranker()
        if reranker.enabled:
            candidates = retriever.retrieve(
                query,
                top_k=reranker.config.candidate_k,
                preferred_article_codes=(
                    rule.preferred_article_codes
                ),
                dedupe_articles=False,
            )
            sources = _finalize_reranked_sources(
                reranker.rerank_with_scores(
                    query=excerpt.strip() or query,
                    candidates=candidates,
                    top_k=4,
                )
            )
        else:
            sources = retriever.retrieve(
                query,
                top_k=4,
                preferred_article_codes=(
                    rule.preferred_article_codes
                ),
            )
        severity, analysis, evidence_status = _analysis(
            rule,
            excerpt,
            sources,
            text,
        )
        recommendation = rule.recommendation
        if (
            rule.key == "probation"
            and excerpt
            and _is_negated_probation_mention(
                _ascii(excerpt)
            )
        ):
            recommendation = (
                "Không cần bổ sung thời hạn hoặc mức lương thử việc "
                "nếu hai bên xác nhận không áp dụng thử việc."
            )
        findings.append(
            FindingDraft(
                category=rule.key,
                title=rule.title,
                severity=severity,
                contract_excerpt=(
                    excerpt
                    or (
                        "Chưa tìm thấy điều khoản liên quan "
                        "trong nội dung được trích xuất."
                    )
                ),
                analysis=analysis,
                recommendation=recommendation,
                evidence_status=evidence_status,
                sources=sources,
            )
        )
    return ReviewDraft(
        summary=build_review_summary(findings),
        findings=findings,
    )


__all__ = [
    "FindingDraft",
    "ReviewDraft",
    "CategoryRule",
    "CATEGORIES",
    "_ascii",
    "_tokens",
    "_string_list",
    "CanonicalEvidenceRetriever",
    "evidence_retriever",
    "_paragraphs",
    "_is_negated_probation_mention",
    "_is_contract_section_heading",
    "_excerpt_for",
    "_plain_text",
    "_has_pay_date",
    "_unit_numbers",
    "_number_near_anchors",
    "_analysis",
    "build_review_summary",
    "_finalize_reranked_sources",
    "review_contract",
]
