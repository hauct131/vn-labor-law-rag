"""Unit tests for the read-only Qdrant operational readiness gate."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.core.qdrant_readiness import evaluate_qdrant_gate


SHA = "a" * 64
ALIAS = "labor_law_active"
COLLECTION = "labor_law_20260727_fd35bb1a"


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        qdrant_url="http://qdrant:6333",
        qdrant_collection=ALIAS,
        qdrant_expected_collection=COLLECTION,
        qdrant_api_key="",
        qdrant_readiness_timeout_seconds=3.0,
        dense_vector_name="dense",
        dense_vector_size=1024,
        sparse_vector_name="sparse",
        retrieval_expected_chunks=833,
        retrieval_corpus_sha256=SHA,
    )


class FakeQdrant:
    def __init__(
        self,
        *,
        alias_target: str | None = COLLECTION,
        collection_status: str = "green",
        point_count: int = 833,
        fingerprint_count: int = 833,
        dense_size: int | None = 1024,
        has_sparse: bool = True,
    ) -> None:
        self.alias_target = alias_target
        self.collection_status = collection_status
        self.point_count = point_count
        self.fingerprint_count = fingerprint_count
        self.dense_size = dense_size
        self.has_sparse = has_sparse

    def __call__(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if method == "GET" and path == "/aliases":
            aliases = []
            if self.alias_target is not None:
                aliases.append({
                    "alias_name": ALIAS,
                    "collection_name": self.alias_target,
                })
            return {"status": "ok", "result": {"aliases": aliases}}

        if method == "GET" and path.startswith("/collections/"):
            vectors = (
                {"dense": {"size": self.dense_size}}
                if self.dense_size is not None
                else {}
            )
            sparse_vectors = {"sparse": {}} if self.has_sparse else {}
            return {
                "status": "ok",
                "result": {
                    "status": self.collection_status,
                    "config": {
                        "params": {
                            "vectors": vectors,
                            "sparse_vectors": sparse_vectors,
                        }
                    },
                },
            }

        if method == "POST" and path.endswith("/points/count"):
            filtered = bool(body and body.get("filter"))
            return {
                "status": "ok",
                "result": {
                    "count": (
                        self.fingerprint_count
                        if filtered
                        else self.point_count
                    )
                },
            }
        raise AssertionError(f"Unexpected request: {method} {path} {body}")


def test_qdrant_gate_accepts_verified_active_collection() -> None:
    report = evaluate_qdrant_gate(settings(), request=FakeQdrant())

    assert report["status"] == "ready"
    assert report["errors"] == []
    assert report["active_collection"] == COLLECTION
    assert report["collection_status"] == "green"
    assert report["exact_point_count"] == 833
    assert report["fingerprint_point_count"] == 833
    assert report["dense_vector_size"] == 1024


def test_qdrant_gate_rejects_missing_alias() -> None:
    report = evaluate_qdrant_gate(
        settings(),
        request=FakeQdrant(alias_target=None),
    )

    assert report["errors"] == ["qdrant_alias_missing"]


def test_qdrant_gate_rejects_wrong_alias_target() -> None:
    report = evaluate_qdrant_gate(
        settings(),
        request=FakeQdrant(alias_target="labor_law"),
    )

    assert report["errors"] == ["qdrant_alias_target_mismatch"]


def test_qdrant_gate_rejects_non_green_collection() -> None:
    report = evaluate_qdrant_gate(
        settings(),
        request=FakeQdrant(collection_status="yellow"),
    )

    assert "qdrant_collection_not_green" in report["errors"]


def test_qdrant_gate_rejects_count_or_fingerprint_mismatch() -> None:
    report = evaluate_qdrant_gate(
        settings(),
        request=FakeQdrant(point_count=832, fingerprint_count=831),
    )

    assert "qdrant_point_count_mismatch" in report["errors"]
    assert "qdrant_fingerprint_count_mismatch" in report["errors"]


def test_qdrant_gate_rejects_invalid_vector_schema() -> None:
    report = evaluate_qdrant_gate(
        settings(),
        request=FakeQdrant(dense_size=768, has_sparse=False),
    )

    assert "qdrant_dense_vector_size_mismatch" in report["errors"]
    assert "qdrant_sparse_vector_missing" in report["errors"]
