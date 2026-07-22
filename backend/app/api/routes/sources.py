import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.source import LegalArticleResponse
from app.services.source_catalog import (
    ArticleNotFoundError,
    LegalSourceCatalog,
    SourceCatalogError,
    get_source_catalog,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sources", tags=["sources"])


def source_catalog_dependency() -> LegalSourceCatalog:
    try:
        return get_source_catalog()
    except SourceCatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.get("/{article_code}", response_model=LegalArticleResponse)
def read_legal_article(
    article_code: str,
    catalog: LegalSourceCatalog = Depends(source_catalog_dependency),
) -> LegalArticleResponse:
    try:
        article = catalog.get_article(article_code)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ArticleNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy điều luật có mã {article_code}.",
        ) from exc
    except SourceCatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    logger.info(
        "legal_article_served article_code=%s chunks=%d source_document_id=%s",
        article.article_code,
        article.chunk_count,
        article.source_document_id,
    )
    return article
