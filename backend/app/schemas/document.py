"""Pydantic schemas for the legal document library API."""

from __future__ import annotations

from pydantic import BaseModel


class ArticleSummary(BaseModel):
    article_code: str
    article_number: int | None = None
    title: str | None = None
    heading: str | None = None
    chapter_number: str | None = None
    chapter_title: str | None = None
    section_number: str | None = None
    section_title: str | None = None


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class DocumentSummary(BaseModel):
    document_id: str
    document_number: str
    title: str | None = None
    source_type: str | None = None
    issuing_authority: str | None = None
    issued_date: str | None = None
    effective_date: str | None = None
    legal_status_code: str | None = None
    article_count: int
    official_url: str | None = None
    source_adapter: str | None = None


class DocumentDetail(DocumentSummary):
    law_as_of: str | None = None
    url_status: str | None = None
    url_last_checked_at: str | None = None


class DocumentListResponse(BaseModel):
    release_id: str
    law_as_of: str | None = None
    pagination: PaginationMeta
    documents: list[DocumentSummary]


class ArticleListResponse(BaseModel):
    document_id: str
    document_number: str
    pagination: PaginationMeta
    articles: list[ArticleSummary]
