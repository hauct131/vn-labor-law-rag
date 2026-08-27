"""Persistence operations for user-owned contract reviews."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.contract_review import (
    ContractReview,
    ContractReviewFinding,
    ContractReviewSource,
)
from app.schemas.ask import LegalSource, RetrievalMethod
from app.schemas.contract_review import (
    ContractReviewFindingResponse,
    ContractReviewListItem,
    ContractReviewResponse,
)
from app.services.contract_review_service import ReviewDraft, build_review_summary


class ContractReviewNotFoundError(LookupError):
    pass


def create_contract_review(
    session: Session,
    *,
    user_id: str,
    filename: str,
    file_sha256: str,
    mime_type: str,
    file_size_bytes: int,
    method: RetrievalMethod,
    extracted_character_count: int,
    draft: ReviewDraft,
) -> ContractReview:
    review = ContractReview(
        user_id=user_id,
        original_filename=filename,
        file_sha256=file_sha256,
        mime_type=mime_type,
        file_size_bytes=file_size_bytes,
        retrieval_method=method.value,
        status="completed",
        summary=draft.summary,
        extracted_character_count=extracted_character_count,
    )
    session.add(review)
    session.flush()
    for position, item in enumerate(draft.findings, 1):
        finding = ContractReviewFinding(
            review_id=review.id,
            position=position,
            category=item.category,
            title=item.title,
            severity=item.severity,
            contract_excerpt=item.contract_excerpt,
            analysis=item.analysis,
            recommendation=item.recommendation,
            evidence_status=item.evidence_status,
        )
        session.add(finding)
        session.flush()
        for source in item.sources:
            session.add(ContractReviewSource(
                finding_id=finding.id,
                source_id=source.source_id,
                chunk_id=source.chunk_id,
                article_code=source.article_code,
                article_number=source.article_number,
                article_title=source.article_title,
                document_title=source.document_title,
                document_number=source.document_number,
                citation_label=source.citation_label,
                clause_number=source.clause_number,
                point_labels=source.point_labels,
                quoted_text=source.content,
                score=source.score,
                source_rank=source.rank,
                retrieval_origin=source.retrieval_origin,
                source_type=source.source_type,
                source_url=source.source_url,
                component_ranks=source.component_ranks,
            ))
    session.commit()
    return _owned_review(session, user_id=user_id, review_id=review.id)


def _owned_review(session: Session, *, user_id: str, review_id: str) -> ContractReview:
    record = session.scalar(
        select(ContractReview)
        .where(ContractReview.id == review_id, ContractReview.user_id == user_id)
        .options(
            selectinload(ContractReview.findings)
            .selectinload(ContractReviewFinding.sources)
        )
    )
    if record is None:
        raise ContractReviewNotFoundError(review_id)
    return record


def _source_response(source: ContractReviewSource) -> LegalSource:
    return LegalSource(
        source_id=source.source_id,
        chunk_id=source.chunk_id,
        article_code=source.article_code,
        article_number=source.article_number,
        article_title=source.article_title,
        document_title=source.document_title,
        document_number=source.document_number,
        citation_label=source.citation_label,
        clause_number=source.clause_number,
        point_labels=list(source.point_labels or []),
        content=source.quoted_text,
        score=source.score,
        rank=source.source_rank,
        retrieval_origin=source.retrieval_origin,
        source_type=source.source_type,
        source_url=source.source_url,
        component_ranks=dict(source.component_ranks or {}),
    )


def contract_review_response(review: ContractReview) -> ContractReviewResponse:
    findings = [
        ContractReviewFindingResponse(
            id=finding.id,
            category=finding.category,
            title=finding.title,
            severity=finding.severity,
            contract_excerpt=finding.contract_excerpt,
            analysis=finding.analysis,
            recommendation=finding.recommendation,
            evidence_status=finding.evidence_status,
            sources=[_source_response(source) for source in finding.sources],
        )
        for finding in review.findings
    ]
    return ContractReviewResponse(
        id=review.id,
        original_filename=review.original_filename,
        file_sha256=review.file_sha256,
        mime_type=review.mime_type,
        file_size_bytes=review.file_size_bytes,
        method=RetrievalMethod(review.retrieval_method),
        status=review.status,
        summary=build_review_summary(findings),
        extracted_character_count=review.extracted_character_count,
        findings=findings,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


def get_contract_review(session: Session, *, user_id: str, review_id: str) -> ContractReviewResponse:
    return contract_review_response(_owned_review(session, user_id=user_id, review_id=review_id))


def list_contract_reviews(
    session: Session, *, user_id: str, limit: int, offset: int
) -> tuple[list[ContractReviewListItem], int]:
    total = int(session.scalar(
        select(func.count()).select_from(ContractReview).where(ContractReview.user_id == user_id)
    ) or 0)
    records = list(session.scalars(
        select(ContractReview)
        .where(ContractReview.user_id == user_id)
        .options(selectinload(ContractReview.findings))
        .order_by(ContractReview.created_at.desc(), ContractReview.id.desc())
        .limit(limit)
        .offset(offset)
    ).all())
    return ([
        ContractReviewListItem(
            id=review.id,
            original_filename=review.original_filename,
            status=review.status,
            summary=build_review_summary(review.findings),
            finding_count=len(review.findings),
            attention_count=sum(
                item.evidence_status != "insufficient_evidence"
                and item.severity == "attention"
                for item in review.findings
            ),
            warning_count=sum(
                item.evidence_status != "insufficient_evidence"
                and item.severity == "warning"
                for item in review.findings
            ),
            missing_count=sum(
                item.evidence_status == "insufficient_evidence"
                for item in review.findings
            ),
            created_at=review.created_at,
        )
        for review in records
    ], total)


def delete_contract_review(session: Session, *, user_id: str, review_id: str) -> None:
    record = session.scalar(select(ContractReview).where(
        ContractReview.id == review_id,
        ContractReview.user_id == user_id,
    ))
    if record is None:
        raise ContractReviewNotFoundError(review_id)
    session.delete(record)
    session.commit()
