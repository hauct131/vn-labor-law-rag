from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_blocks_unapproved_candidate_release() -> None:
    response = client.get("/api/ready")
    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["release_id"] == "labor-law-2026-07-27-candidate"
    assert payload["chunk_count"] == 778
    assert payload["errors"] == ["authority_review_pending"]
