import pytest
from fastapi.testclient import TestClient

from app.api.routes.documents import document_catalog_dependency
from app.api.routes.sources import source_catalog_dependency
from app.main import app
from app.schemas.document import (
    ArticleListResponse,
    DocumentDetail,
    DocumentListResponse,
)
from app.schemas.source import LegalArticleResponse
from app.services.source_catalog import (
    DocumentNotFoundError,
    SourceCatalogError,
    _normalize_for_search,
)


class _FakeDocumentCatalog:
    def list_documents(
        self,
        *,
        q: str | None = None,
        document_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ):
        docs = [
            DocumentDetail(
                document_id="vn:bll-2019",
                document_number="45/2019/QH14",
                title="Bộ luật Lao động",
                source_type="LQ",
                issuing_authority="Quốc hội",
                issued_date="2019-11-20",
                effective_date="2021-01-01",
                legal_status_code="in_force",
                article_count=220,
                official_url="https://vanban.chinhphu.vn/?docid=198540",
                source_adapter="official_gazette_word",
                law_as_of="2026-07-27",
            ),
            DocumentDetail(
                document_id="vn:nd-145",
                document_number="145/2020/NĐ-CP",
                title="Nghị định 145/2020/NĐ-CP",
                source_type="NĐ",
                issuing_authority="Chính phủ",
                issued_date="2020-12-14",
                effective_date="2021-02-01",
                legal_status_code="in_force",
                article_count=115,
                official_url="https://vanban.chinhphu.vn/?docid=202470",
                source_adapter="official_gazette_word",
                law_as_of="2026-07-27",
            ),
        ]

        if document_type and document_type.strip():
            dt = document_type.strip().casefold()
            docs = [d for d in docs if d.source_type.casefold() == dt]

        if q and q.strip():
            query_norm = _normalize_for_search(q.strip())
            docs = [
                d for d in docs
                if query_norm in _normalize_for_search(d.document_number + " " + (d.title or ""))
            ]

        total = len(docs)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        start = (page - 1) * page_size
        page_docs = docs[start : start + page_size]

        return DocumentListResponse(
            release_id="test-release-2026",
            law_as_of="2026-07-27",
            pagination={
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
            documents=[d for d in page_docs],
        )

    def get_document(self, document_id: str) -> DocumentDetail:
        if document_id == "vn:bll-2019":
            return DocumentDetail(
                document_id="vn:bll-2019",
                document_number="45/2019/QH14",
                title="Bộ luật Lao động",
                source_type="LQ",
                issuing_authority="Quốc hội",
                issued_date="2019-11-20",
                effective_date="2021-01-01",
                legal_status_code="in_force",
                article_count=220,
                official_url="https://vanban.chinhphu.vn/?docid=198540",
                source_adapter="official_gazette_word",
                law_as_of="2026-07-27",
            )
        raise DocumentNotFoundError(document_id)

    def list_document_articles(
        self,
        document_id: str,
        *,
        q: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ):
        if document_id != "vn:bll-2019":
            raise DocumentNotFoundError(document_id)

        articles = [
            {
                "article_code": "20.2.LQ.169",
                "article_number": 169,
                "title": "Tuổi nghỉ hưu",
                "heading": None,
                "chapter_number": "XII",
                "chapter_title": "BẢO HIỂM XÃ HỘI",
                "section_number": None,
                "section_title": None,
            },
            {
                "article_code": "20.2.LQ.170",
                "article_number": 170,
                "title": "Bảo hiểm xã hội",
                "heading": None,
                "chapter_number": "XII",
                "chapter_title": "BẢO HIỂM XÃ HỘI",
                "section_number": None,
                "section_title": None,
            },
        ]

        if q and q.strip():
            query_norm = _normalize_for_search(q.strip())
            articles = [
                a for a in articles
                if query_norm in _normalize_for_search(
                    str(a["article_number"] or "") + " " + (a["title"] or "") + " " + (a["heading"] or "")
                )
            ]

        total = len(articles)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        start = (page - 1) * page_size
        page_arts = articles[start : start + page_size]

        return ArticleListResponse(
            document_id="vn:bll-2019",
            document_number="45/2019/QH14",
            pagination={
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
            articles=page_arts,
        )


@pytest.fixture
def client():
    app.dependency_overrides[document_catalog_dependency] = lambda: _FakeDocumentCatalog()
    app.dependency_overrides[source_catalog_dependency] = lambda: _FakeDocumentCatalog()
    yield TestClient(app)
    app.dependency_overrides.clear()


# ----------------------------------------------------------------------
# C. TEST API ENDPOINTS & VALIDATIONS
# ----------------------------------------------------------------------

def test_get_documents_success(client):
    res = client.get("/api/documents")
    assert res.status_code == 200
    data = res.json()
    assert data["release_id"] == "test-release-2026"
    assert data["pagination"]["total"] == 2
    assert len(data["documents"]) == 2
    assert data["documents"][0]["source_adapter"] == "official_gazette_word"


def test_get_documents_q_number(client):
    res = client.get("/api/documents?q=145/2020")
    assert res.status_code == 200
    data = res.json()
    assert data["pagination"]["total"] == 1
    assert data["documents"][0]["document_id"] == "vn:nd-145"


def test_get_documents_q_accented(client):
    res = client.get("/api/documents?q=Bộ luật Lao động")
    assert res.status_code == 200
    data = res.json()
    assert data["pagination"]["total"] == 1
    assert data["documents"][0]["document_id"] == "vn:bll-2019"


def test_get_documents_q_unaccented(client):
    res = client.get("/api/documents?q=bo%20luat%20lao%20dong")
    assert res.status_code == 200
    data = res.json()
    assert data["pagination"]["total"] == 1
    assert data["documents"][0]["document_id"] == "vn:bll-2019"


def test_get_documents_q_whitespace_only(client):
    res = client.get("/api/documents?q=%20%20%20")
    assert res.status_code == 200
    assert res.json()["pagination"]["total"] == 2


def test_get_documents_document_type_filter(client):
    res = client.get("/api/documents?document_type=NĐ")
    assert res.status_code == 200
    data = res.json()
    assert data["pagination"]["total"] == 1
    assert data["documents"][0]["source_type"] == "NĐ"


def test_get_documents_pagination_metadata(client):
    res = client.get("/api/documents?page=1&page_size=1")
    assert res.status_code == 200
    p = res.json()["pagination"]
    assert p["page"] == 1
    assert p["page_size"] == 1
    assert p["total"] == 2
    assert p["total_pages"] == 2


def test_get_documents_invalid_page_size_zero(client):
    res = client.get("/api/documents?page_size=0")
    assert res.status_code == 422


def test_get_documents_invalid_page_size_too_large(client):
    res = client.get("/api/documents?page_size=101")
    assert res.status_code == 422


def test_get_documents_invalid_page_zero(client):
    res = client.get("/api/documents?page=0")
    assert res.status_code == 422


def test_get_documents_q_too_long(client):
    res = client.get(f"/api/documents?q={'a'*201}")
    assert res.status_code == 422


def test_get_document_by_id_success(client):
    res = client.get("/api/documents/vn:bll-2019")
    assert res.status_code == 200
    data = res.json()
    assert data["document_id"] == "vn:bll-2019"
    assert data["article_count"] == 220


def test_get_document_by_id_url_encoded_colon(client):
    res = client.get("/api/documents/vn%3Abll-2019")
    assert res.status_code == 200
    assert res.json()["document_id"] == "vn:bll-2019"


def test_get_document_by_id_not_found(client):
    res = client.get("/api/documents/vn:nonexistent")
    assert res.status_code == 404
    assert "Không tìm thấy" in res.json()["detail"]


def test_get_document_articles_success(client):
    res = client.get("/api/documents/vn:bll-2019/articles")
    assert res.status_code == 200
    data = res.json()
    assert data["document_id"] == "vn:bll-2019"
    assert data["document_number"] == "45/2019/QH14"
    assert data["pagination"]["total"] == 2
    assert data["articles"][0]["article_code"] == "20.2.LQ.169"
    assert data["articles"][0]["title"] == "Tuổi nghỉ hưu"
    assert "article_title" not in data["articles"][0]
    assert "citation_label" not in data["articles"][0]
    assert "document_title" not in data


def test_get_document_articles_search(client):
    res = client.get("/api/documents/vn:bll-2019/articles?q=169")
    assert res.status_code == 200
    assert res.json()["pagination"]["total"] == 1
    assert res.json()["articles"][0]["article_code"] == "20.2.LQ.169"


def test_get_document_articles_pagination(client):
    res = client.get("/api/documents/vn:bll-2019/articles?page=1&page_size=1")
    assert res.status_code == 200
    assert res.json()["pagination"]["page_size"] == 1


def test_get_document_articles_doc_not_found(client):
    res = client.get("/api/documents/vn:nonexistent/articles")
    assert res.status_code == 404


def test_documents_api_service_error_returns_503(monkeypatch):
    def _failing_get_catalog():
        raise SourceCatalogError("Không thể đọc /media/hao/private/canonical_articles.json")

    monkeypatch.setattr("app.api.routes.documents.get_source_catalog", _failing_get_catalog)

    test_client = TestClient(app)
    for endpoint in ["/api/documents", "/api/documents/vn:bll-2019"]:
        res = test_client.get(endpoint)
        assert res.status_code == 503
        detail = res.json()["detail"]
        assert detail == "Dữ liệu thư viện văn bản hiện không khả dụng."
        assert "/media/hao" not in detail
        assert "/home/hao" not in detail
        assert "canonical_articles.json" not in detail
        assert "Không thể đọc" not in detail


def test_sources_api_regression_existing_endpoint():
    class _FakeSourceCatalog:
        def get_article(self, article_code):
            return LegalArticleResponse(
                article_code=article_code,
                article_number="169",
                article_title="Tuổi nghỉ hưu",
                citation_label="Điều 169 Bộ luật Lao động số 45/2019/QH14",
                document_title="Bộ luật Lao động",
                document_number="45/2019/QH14",
                source_type="LQ",
                source_document_id="vbpl:item:139264",
                source_note_text=None,
                topic_code="20.2",
                topic_name="Lao động",
                chapter_number="XII",
                chapter_title="BẢO HIỂM XÃ HỘI",
                section_number=None,
                section_title=None,
                official_url="https://vanban.chinhphu.vn/?docid=198540",
                original_source_urls=[],
                url_status="active",
                url_last_checked_at="2026-07-22",
                chunk_count=1,
                units=[],
            )

    app.dependency_overrides[source_catalog_dependency] = lambda: _FakeSourceCatalog()
    try:
        res = TestClient(app).get("/api/sources/20.2.LQ.169")
        assert res.status_code == 200
        assert res.json()["article_code"] == "20.2.LQ.169"
    finally:
        app.dependency_overrides.clear()


def test_openapi_schema_contains_all_document_routes(client):
    res = client.get("/openapi.json")
    assert res.status_code == 200
    paths = res.json()["paths"]
    assert "/api/documents" in paths
    assert "/api/documents/{document_id}" in paths
    assert "/api/documents/{document_id}/articles" in paths
    assert "/api/sources/{article_code}" in paths
