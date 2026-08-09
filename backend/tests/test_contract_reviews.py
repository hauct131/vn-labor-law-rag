"""API-level tests for real contract extraction, review, persistence, and isolation."""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes.auth import router as auth_router
from app.api.routes.contract_reviews import router as contract_review_router
from app.api.routes.health import (
    authorized_corpus_dependency,
    require_authorized_corpus,
)
from app.core.config import settings
from app.db.database import Base, get_db_session
from app.models import contract_review as _contract_models  # noqa: F401
from app.models import conversation as _conversation_models  # noqa: F401


@pytest.fixture
def app_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app = FastAPI()
    app.include_router(auth_router, prefix="/api")
    app.include_router(contract_review_router, prefix="/api")

    def override_session() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    original_iterations = settings.password_pbkdf2_iterations
    settings.password_pbkdf2_iterations = 1000
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[require_authorized_corpus] = lambda: None
    try:
        with TestClient(app) as client:
            yield client
    finally:
        settings.password_pbkdf2_iterations = original_iterations
        engine.dispose()


def register(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "MatKhauAnToan123",
            "display_name": email.split("@", 1)[0],
        },
    )
    assert response.status_code == 201, response.text
    csrf = client.cookies.get(settings.csrf_cookie_name)
    assert csrf
    return {"X-CSRF-Token": csrf}


def sample_path() -> Path:
    return Path(__file__).resolve().parents[2] / "samples/contract-review/sample_labor_contract.docx"


def test_contract_review_real_docx_and_user_isolation(app_client: TestClient) -> None:
    csrf_a = register(app_client, "contract-a@example.test")
    path = sample_path()
    with path.open("rb") as source:
        response = app_client.post(
            "/api/contract-reviews",
            headers=csrf_a,
            data={"method": "sparse"},
            files={
                "file": (
                    path.name,
                    source,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["original_filename"] == path.name
    assert body["status"] == "completed"
    assert len(body["file_sha256"]) == 64
    assert body["extracted_character_count"] > 500
    assert {item["category"] for item in body["findings"]} == {
        "probation",
        "salary",
        "working_time",
        "termination",
    }
    for finding in body["findings"]:
        assert finding["contract_excerpt"]
        assert finding["analysis"]
        assert finding["recommendation"]
        assert finding["sources"]
        source_ids = {source["source_id"] for source in finding["sources"]}
        markers = {f"[{source_id}]" for source_id in source_ids}
        assert any(marker in finding["analysis"] for marker in markers)

    findings = {item["category"]: item for item in body["findings"]}
    assert findings["probation"]["severity"] == "attention"
    assert "75 ngày" in findings["probation"]["contract_excerpt"]
    assert "20.2.LQ.26" in {source["article_code"] for source in findings["probation"]["sources"]}
    assert findings["salary"]["severity"] == "info"
    assert "12.000.000 đồng" in findings["salary"]["contract_excerpt"]
    assert findings["working_time"]["severity"] == "attention"
    assert "9 giờ" in findings["working_time"]["contract_excerpt"]
    assert "45 giờ/tuần" in findings["working_time"]["analysis"]
    assert findings["termination"]["severity"] == "warning"
    assert "10 ngày" in findings["termination"]["contract_excerpt"]

    review_id = body["id"]
    listing = app_client.get("/api/contract-reviews")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == review_id

    detail = app_client.get(f"/api/contract-reviews/{review_id}")
    assert detail.status_code == 200
    assert detail.json()["file_sha256"] == body["file_sha256"]

    logout = app_client.post("/api/auth/logout", headers=csrf_a)
    assert logout.status_code == 204
    csrf_b = register(app_client, "contract-b@example.test")
    assert app_client.get(f"/api/contract-reviews/{review_id}").status_code == 404
    assert app_client.delete(
        f"/api/contract-reviews/{review_id}", headers=csrf_b
    ).status_code == 404


def test_contract_review_rejects_unsupported_and_empty_files(app_client: TestClient) -> None:
    csrf = register(app_client, "contract-validation@example.test")
    unsupported = app_client.post(
        "/api/contract-reviews",
        headers=csrf,
        data={"method": "sparse"},
        files={"file": ("contract.txt", b"plain text", "text/plain")},
    )
    assert unsupported.status_code == 422
    assert "PDF hoặc DOCX" in unsupported.json()["detail"]

    empty = app_client.post(
        "/api/contract-reviews",
        headers=csrf,
        data={"method": "sparse"},
        files={
            "file": (
                "empty.docx",
                b"",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert empty.status_code == 422
    assert "trống" in empty.json()["detail"]


def test_pdf_extraction_uses_real_text_layer() -> None:
    from app.services.contract_file_extraction import extract_contract
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    path = Path(__file__).resolve().parents[2] / "samples/contract-review/sample_labor_contract.pdf"
    result = extract_contract(path.name, "application/pdf", path.read_bytes())
    assert result.mime_type == "application/pdf"
    assert "Thu viec 75 ngay" in result.text
    assert len(result.text) > 300
    draft = review_contract(result.text, RetrievalMethod.SPARSE)
    salary = next(item for item in draft.findings if item.category == "salary")
    assert salary.severity == "info"
    assert "12.000.000 dong" in salary.contract_excerpt


def test_contract_review_rejects_unimplemented_retrieval_mode(app_client: TestClient) -> None:
    csrf = register(app_client, "contract-mode@example.test")
    path = sample_path()
    with path.open("rb") as source:
        response = app_client.post(
            "/api/contract-reviews",
            headers=csrf,
            data={"method": "hybrid"},
            files={
                "file": (
                    path.name,
                    source,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
    assert response.status_code == 422
    assert "chỉ hỗ trợ" in response.json()["detail"]


def test_contract_review_rejects_oversized_file_before_parsing(app_client: TestClient) -> None:
    csrf = register(app_client, "contract-large@example.test")
    response = app_client.post(
        "/api/contract-reviews",
        headers=csrf,
        data={"method": "sparse"},
        files={
            "file": (
                "large.docx",
                b"x" * (10 * 1024 * 1024 + 1),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 422
    assert "10 MB" in response.json()["detail"]


def test_contract_review_blocks_unapproved_corpus(app_client: TestClient) -> None:
    csrf = register(app_client, "contract-gate@example.test")
    app_client.app.dependency_overrides.pop(require_authorized_corpus)
    app_client.app.dependency_overrides[authorized_corpus_dependency] = lambda: {
        "status": "not_ready",
        "release_id": "candidate",
        "release_status": "pending",
        "chunk_count": 804,
        "chunk_sha256": "a" * 64,
        "errors": ["authority_review_pending"],
    }
    path = sample_path()
    with path.open("rb") as source:
        response = app_client.post(
            "/api/contract-reviews",
            headers=csrf,
            data={"method": "sparse"},
            files={
                "file": (
                    path.name,
                    source,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "release_not_ready",
        "release_id": "candidate",
        "errors": ["authority_review_pending"],
    }


def test_contract_review_binds_numbers_to_their_legal_terms() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    draft = review_contract(
        """
        HỢP ĐỒNG LAO ĐỘNG XÁC ĐỊNH THỜI HẠN 24 tháng
        Thử việc 30 ngày; khoản thanh toán hoàn tất trong 90 ngày.
        Tiền lương 12.000.000 đồng, trả vào ngày 05 hằng tháng.
        Thời giờ làm việc 8 giờ/ngày, 48 giờ/tuần.
        Khi chấm dứt hợp đồng phải báo trước 45 ngày; nghỉ phép riêng 1 ngày.
        """,
        RetrievalMethod.SPARSE,
    )
    findings = {item.category: item for item in draft.findings}

    assert findings["probation"].severity == "info"
    assert "30 ngày" in findings["probation"].analysis
    assert "90 ngày" not in findings["probation"].analysis
    assert findings["working_time"].severity == "info"
    assert "8 giờ/ngày" in findings["working_time"].analysis
    assert "48 giờ/ngày" not in findings["working_time"].analysis
    assert "45 ngày" in findings["termination"].analysis
    assert "1 ngày" not in findings["termination"].analysis


def test_contract_review_recognizes_any_fixed_term_up_to_36_months() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    draft = review_contract(
        """
        HỢP ĐỒNG LAO ĐỘNG XÁC ĐỊNH THỜI HẠN 24 tháng
        Thử việc 30 ngày.
        Tiền lương 12.000.000 đồng, trả vào ngày 05 hằng tháng.
        Thời giờ làm việc 8 giờ/ngày, 48 giờ/tuần.
        Khi chấm dứt hợp đồng, mỗi bên phải báo trước 10 ngày.
        """,
        RetrievalMethod.SPARSE,
    )
    termination = next(item for item in draft.findings if item.category == "termination")

    assert termination.severity == "warning"
    assert "24 tháng" in termination.analysis
    assert "10 ngày" in termination.analysis


def test_contract_review_does_not_invent_categories_from_unrelated_numbers() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    draft = review_contract(
        """
        Số hợp đồng 123/2026.
        Người lao động: Nguyễn Văn A.
        Căn cước công dân số 012345678901, cấp ngày 01/01/2026.
        """,
        RetrievalMethod.SPARSE,
    )

    assert "4 nhóm chưa tìm thấy" in draft.summary
    for finding in draft.findings:
        assert finding.severity == "attention"
        assert finding.contract_excerpt.startswith("Chưa tìm thấy")


def test_contract_review_sources_are_unique_and_all_are_cited() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    draft = review_contract(
        """
        HỢP ĐỒNG LAO ĐỘNG XÁC ĐỊNH THỜI HẠN 24 tháng.
        Thử việc 30 ngày, lương thử việc bằng 85 phần trăm tiền lương chính thức.
        Tiền lương 12.000.000 đồng, trả vào ngày 05 hằng tháng.
        Thời giờ làm việc 8 giờ/ngày, 48 giờ/tuần và nghỉ hằng tuần.
        Khi chấm dứt hợp đồng, mỗi bên phải báo trước 45 ngày.
        """,
        RetrievalMethod.SPARSE,
    )

    for finding in draft.findings:
        article_codes = [source.article_code for source in finding.sources]
        assert len(article_codes) == len(set(article_codes))
        for source in finding.sources:
            assert f"[{source.source_id}]" in finding.analysis

    termination = next(
        finding for finding in draft.findings if finding.category == "termination"
    )
    assert {source.article_code for source in termination.sources} == {
        "20.2.LQ.34",
        "20.2.LQ.35",
        "20.2.LQ.36",
    }


def test_probation_salary_does_not_replace_the_main_salary_clause() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    draft = review_contract(
        """
        Hợp đồng lao động số 123/2026.
        Thử việc 30 ngày, lương thử việc bằng 85 phần trăm lương chính thức.
        """,
        RetrievalMethod.SPARSE,
    )
    salary = next(finding for finding in draft.findings if finding.category == "salary")

    assert salary.severity == "attention"
    assert salary.contract_excerpt.startswith("Chưa tìm thấy")
