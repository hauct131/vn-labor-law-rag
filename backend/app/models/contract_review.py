"""Relational models for persisted labor contract reviews."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def _uuid() -> str:
    return str(uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContractReview(Base):
    __tablename__ = "contract_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_method: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    findings: Mapped[list[ContractReviewFinding]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        order_by="ContractReviewFinding.position, ContractReviewFinding.id",
    )


class ContractReviewFinding(Base):
    __tablename__ = "contract_review_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    review_id: Mapped[str] = mapped_column(
        ForeignKey("contract_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    contract_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    analysis: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    review: Mapped[ContractReview] = relationship(back_populates="findings")
    sources: Mapped[list[ContractReviewSource]] = relationship(
        back_populates="finding",
        cascade="all, delete-orphan",
        order_by="ContractReviewSource.source_rank, ContractReviewSource.id",
    )


class ContractReviewSource(Base):
    __tablename__ = "contract_review_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    finding_id: Mapped[str] = mapped_column(
        ForeignKey("contract_review_findings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[str | None] = mapped_column(String(50))
    chunk_id: Mapped[str] = mapped_column(String(200), nullable=False)
    article_code: Mapped[str | None] = mapped_column(String(200), index=True)
    article_number: Mapped[str | None] = mapped_column(String(80))
    article_title: Mapped[str | None] = mapped_column(String(500))
    document_title: Mapped[str | None] = mapped_column(String(500))
    document_number: Mapped[str | None] = mapped_column(String(200))
    citation_label: Mapped[str | None] = mapped_column(String(500))
    clause_number: Mapped[str | None] = mapped_column(String(80))
    point_labels: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list, nullable=False
    )
    quoted_text: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    source_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_origin: Mapped[str | None] = mapped_column(String(100))
    source_type: Mapped[str | None] = mapped_column(String(50))
    source_url: Mapped[str | None] = mapped_column(Text)
    component_ranks: Mapped[dict[str, int]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )

    finding: Mapped[ContractReviewFinding] = relationship(back_populates="sources")
