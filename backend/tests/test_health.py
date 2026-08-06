from fastapi.testclient import TestClient

from app.api.routes.health import (
    qdrant_gate_dependency,
    release_gate_dependency,
    runtime_gate_dependency,
)
from app.core.config import settings
from app.main import app

client = TestClient(app)

QDRANT_READY = {
    "status": "ready",
    "alias": "labor_law_active",
    "expected_collection": "labor_law_20260728_fd35bb1a",
    "active_collection": "labor_law_20260728_fd35bb1a",
    "collection_status": "green",
    "exact_point_count": 833,
    "fingerprint_point_count": 833,
    "dense_vector_name": "dense",
    "dense_vector_size": 1024,
    "sparse_vector_name": "sparse",
    "errors": [],
}

RUNTIME_READY = {
    "status": "ready",
    "components": {
        "java": {"status": "ready"},
        "vncorenlp_assets": {"status": "ready"},
        "sparse_retriever": {"status": "ready"},
        "dense_retriever": {"status": "ready"},
        "generation": {"status": "ready"},
    },
    "errors": [],
}


def test_liveness_and_legacy_health() -> None:
    for path in ("/api/live", "/api/health"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_readiness_blocks_unapproved_candidate_release(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "corpus_require_authority_approval",
        True,
    )
    app.dependency_overrides[qdrant_gate_dependency] = lambda: QDRANT_READY
    app.dependency_overrides[runtime_gate_dependency] = lambda: RUNTIME_READY
    try:
        response = client.get("/api/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["release_id"] == "labor-law-canonical-word-20260804-164432-candidate"
    assert payload["chunk_count"] == 804
    assert payload["errors"] == [
        "authority_review_pending",
        "release_not_publishable",
    ]
    assert payload["qdrant"] == QDRANT_READY
    assert payload["runtime"] == RUNTIME_READY


def test_readiness_includes_qdrant_failure() -> None:
    qdrant_failure = {
        **QDRANT_READY,
        "status": "not_ready",
        "errors": ["qdrant_point_count_mismatch"],
        "exact_point_count": 832,
    }
    app.dependency_overrides[qdrant_gate_dependency] = (
        lambda: qdrant_failure
    )
    app.dependency_overrides[runtime_gate_dependency] = lambda: RUNTIME_READY
    try:
        response = client.get("/api/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "qdrant_point_count_mismatch" in response.json()["errors"]
    assert response.json()["qdrant"]["exact_point_count"] == 832


def test_readiness_includes_runtime_failure() -> None:
    runtime_failure = {
        **RUNTIME_READY,
        "status": "not_ready",
        "errors": ["runtime_vncorenlp_jar_missing"],
    }
    app.dependency_overrides[qdrant_gate_dependency] = lambda: QDRANT_READY
    app.dependency_overrides[runtime_gate_dependency] = (
        lambda: runtime_failure
    )
    try:
        response = client.get("/api/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "runtime_vncorenlp_jar_missing" in response.json()["errors"]
    assert response.json()["runtime"]["status"] == "not_ready"


def test_readiness_returns_200_for_approved_report() -> None:
    app.dependency_overrides[release_gate_dependency] = lambda: {
        "status": "ready",
        "release_id": "approved-release",
        "release_status": "production",
        "chunk_count": 833,
        "chunk_sha256": "a" * 64,
        "errors": [],
    }
    try:
        response = client.get("/api/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
