"""Read-only operational readiness checks for the active Qdrant index."""

from __future__ import annotations

import urllib.parse
from typing import Any

from app.core.config import settings
from app.ingestion.qdrant_alias import (
    AliasActivationError,
    JsonRequest,
    _alias_map,
    _exact_count,
    _json_request_factory,
)


def _collection_schema(
    request: JsonRequest,
    collection: str,
) -> dict[str, Any]:
    quoted = urllib.parse.quote(collection, safe="")
    payload = request("GET", f"/collections/{quoted}", None)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise AliasActivationError(
            f"Qdrant collection response for '{collection}' has no result"
        )
    return result


def evaluate_qdrant_gate(
    settings_obj: Any = settings,
    *,
    request: JsonRequest | None = None,
) -> dict[str, Any]:
    """Verify the alias, collection health, counts, fingerprint, and schema."""
    alias = settings_obj.qdrant_collection
    expected_collection = settings_obj.qdrant_expected_collection
    errors: list[str] = []
    report: dict[str, Any] = {
        "status": "not_ready",
        "alias": alias,
        "expected_collection": expected_collection,
        "active_collection": None,
        "collection_status": None,
        "exact_point_count": None,
        "fingerprint_point_count": None,
        "dense_vector_name": settings_obj.dense_vector_name,
        "dense_vector_size": None,
        "sparse_vector_name": settings_obj.sparse_vector_name,
        "errors": errors,
    }

    try:
        active_request = request or _json_request_factory(
            settings_obj.qdrant_url,
            settings_obj.qdrant_api_key or None,
            settings_obj.qdrant_readiness_timeout_seconds,
        )
        active_collection = _alias_map(active_request).get(alias)
        report["active_collection"] = active_collection

        if active_collection is None:
            errors.append("qdrant_alias_missing")
            return report
        if active_collection != expected_collection:
            errors.append("qdrant_alias_target_mismatch")
            return report

        details = _collection_schema(active_request, active_collection)
        collection_status = details.get("status")
        report["collection_status"] = collection_status
        if collection_status != "green":
            errors.append("qdrant_collection_not_green")

        params = details.get("config", {}).get("params", {})
        vectors = params.get("vectors")
        dense = (
            vectors.get(settings_obj.dense_vector_name)
            if isinstance(vectors, dict)
            else None
        )
        if not isinstance(dense, dict):
            errors.append("qdrant_dense_vector_missing")
        else:
            dense_size = dense.get("size")
            report["dense_vector_size"] = dense_size
            if dense_size != settings_obj.dense_vector_size:
                errors.append("qdrant_dense_vector_size_mismatch")

        sparse_vectors = params.get("sparse_vectors")
        if (
            not isinstance(sparse_vectors, dict)
            or settings_obj.sparse_vector_name not in sparse_vectors
        ):
            errors.append("qdrant_sparse_vector_missing")

        total_count = _exact_count(active_request, active_collection)
        fingerprint_count = _exact_count(
            active_request,
            active_collection,
            corpus_sha256=settings_obj.retrieval_corpus_sha256,
        )
        report["exact_point_count"] = total_count
        report["fingerprint_point_count"] = fingerprint_count

        if total_count != settings_obj.retrieval_expected_chunks:
            errors.append("qdrant_point_count_mismatch")
        if fingerprint_count != settings_obj.retrieval_expected_chunks:
            errors.append("qdrant_fingerprint_count_mismatch")
    except Exception as exc:
        errors.append("qdrant_probe_failed")
        report["probe_error"] = str(exc)

    if not errors:
        report["status"] = "ready"
    return report
