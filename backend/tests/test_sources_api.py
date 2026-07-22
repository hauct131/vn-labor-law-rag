from fastapi.testclient import TestClient

from app.api.routes.sources import source_catalog_dependency
from app.main import app
from app.schemas.source import LegalArticleResponse


class _FakeCatalog:
    def get_article(self, article_code):
        if article_code == "20.2.LQ.404":
            from app.services.source_catalog import ArticleNotFoundError

            raise ArticleNotFoundError(article_code)
        return LegalArticleResponse(**{
            "article_code": "20.2.LQ.169",
            "article_number": "169",
            "article_title": "Tuổi nghỉ hưu",
            "citation_label": "Điều 169 Bộ luật Lao động số 45/2019/QH14",
            "document_title": "Bộ luật Lao động",
            "document_number": "45/2019/QH14",
            "source_type": "LQ",
            "source_document_id": "vbpl:item:139264",
            "source_note_text": None,
            "topic_code": "20.2",
            "topic_name": "Lao động",
            "chapter_number": "XII",
            "chapter_title": "BẢO HIỂM XÃ HỘI",
            "section_number": None,
            "section_title": None,
            "official_url": "https://vanban.chinhphu.vn/?docid=198540&pageid=27160",
            "original_source_urls": [],
            "url_status": "active",
            "url_last_checked_at": "2026-07-22",
            "chunk_count": 1,
            "units": [{
                "chunk_id": "chunk-1",
                "label": "Khoản 1",
                "unit_type": "clause",
                "clause_number": "1",
                "point_labels": [],
                "table_index": None,
                "segment_index": None,
                "content": "1. Nội dung.",
            }],
        })


def test_sources_api_returns_internal_article():
    app.dependency_overrides[source_catalog_dependency] = lambda: _FakeCatalog()
    try:
        response = TestClient(app).get("/api/sources/20.2.LQ.169")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["article_code"] == "20.2.LQ.169"
    assert payload["chunk_count"] == 1
    assert payload["units"][0]["content"] == "1. Nội dung."
    assert payload["official_url"].startswith("https://vanban.chinhphu.vn/")


def test_sources_api_returns_404_for_unknown_article():
    app.dependency_overrides[source_catalog_dependency] = lambda: _FakeCatalog()
    try:
        response = TestClient(app).get("/api/sources/20.2.LQ.404")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert "20.2.LQ.404" in response.json()["detail"]
