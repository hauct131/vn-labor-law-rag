"""Document library endpoints — read-only, no retrieval stack required."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.schemas.document import (
    ArticleListResponse,
    DocumentDetail,
    DocumentListResponse,
)
from app.services.source_catalog import (
    DocumentNotFoundError,
    LegalSourceCatalog,
    SourceCatalogError,
    get_source_catalog,
)


router = APIRouter(prefix="/documents", tags=["documents"])


def _catalog() -> LegalSourceCatalog:
    try:
        return get_source_catalog()
    except SourceCatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dữ liệu thư viện văn bản hiện không khả dụng.",
        ) from exc


document_catalog_dependency = _catalog


@router.get("", response_model=DocumentListResponse)
def list_documents(
    q: str | None = Query(default=None, max_length=200),
    document_type: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    catalog: LegalSourceCatalog = Depends(_catalog),
) -> DocumentListResponse:
    try:
        return catalog.list_documents(
            q=q or None,
            document_type=document_type or None,
            page=page,
            page_size=page_size,
        )
    except SourceCatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không thể đọc danh sách văn bản pháp luật.",
        ) from exc


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: str,
    catalog: LegalSourceCatalog = Depends(_catalog),
) -> DocumentDetail:
    try:
        return catalog.get_document(document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy văn bản có mã '{document_id}'.",
        ) from exc
    except SourceCatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không thể đọc thông tin văn bản pháp luật.",
        ) from exc


@router.get("/{document_id}/articles", response_model=ArticleListResponse)
def list_document_articles(
    document_id: str,
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    catalog: LegalSourceCatalog = Depends(_catalog),
) -> ArticleListResponse:
    try:
        return catalog.list_document_articles(
            document_id,
            q=q or None,
            page=page,
            page_size=page_size,
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy văn bản có mã '{document_id}'.",
        ) from exc
    except SourceCatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không thể đọc danh sách điều luật.",
        ) from exc
