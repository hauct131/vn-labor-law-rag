import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.qdrant_readiness import evaluate_qdrant_gate
from app.core.runtime_readiness import evaluate_cached_runtime_gate
from app.schemas.health import HealthResponse

router = APIRouter(tags=["Health"])


@router.get("/live", response_model=HealthResponse)
async def liveness_check() -> HealthResponse:
    """Return success when the API process can serve HTTP requests."""
    return HealthResponse(status="ok")


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Backward-compatible alias for the process liveness endpoint."""
    return HealthResponse(status="ok")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def evaluate_release_gate(settings_obj: Any = settings) -> dict[str, Any]:
    """Validate the immutable corpus release used by question answering."""
    chunks_path = Path(settings_obj.legal_chunks_path)
    manifest_path = Path(settings_obj.corpus_release_manifest_path)
    errors: list[str] = []
    manifest: dict = {}

    if not chunks_path.is_file():
        errors.append(f"missing_chunks:{chunks_path}")
    if not manifest_path.is_file():
        errors.append(f"missing_manifest:{manifest_path}")
    else:
        try:
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            errors.append(f"invalid_manifest:{exc}")

    actual_count = None
    actual_hash = None
    if chunks_path.is_file():
        actual_count = sum(
            1
            for line in chunks_path.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        )
        actual_hash = _sha256(chunks_path)
        if actual_count != settings_obj.retrieval_expected_chunks:
            errors.append(
                "chunk_count_mismatch:"
                f"{actual_count}!={settings_obj.retrieval_expected_chunks}"
            )
        if actual_hash != settings_obj.retrieval_corpus_sha256:
            errors.append("chunk_hash_mismatch")

    if manifest:
        if manifest.get("release_id") != settings_obj.corpus_release_id:
            errors.append("release_id_mismatch")
        manifest_chunk_count = manifest.get("counts", {}).get("chunks")
        if (
            actual_count is not None
            and manifest_chunk_count != actual_count
        ):
            errors.append("manifest_chunk_count_mismatch")
        manifest_chunk_hash = (
            manifest.get("hashes", {}).get("chunks_sha256")
        )
        if actual_hash and manifest_chunk_hash != actual_hash:
            errors.append("manifest_chunk_hash_mismatch")
        if (
            settings_obj.corpus_require_authority_approval
            and not manifest.get("gates", {}).get(
                "authority_review_passed", False
            )
        ):
            errors.append("authority_review_pending")
        if (
            settings_obj.corpus_require_authority_approval
            and not manifest.get("gates", {}).get(
                "production_publishable", False
            )
        ):
            errors.append("release_not_publishable")

    ready = not errors
    return {
        "status": "ready" if ready else "not_ready",
        "release_id": manifest.get("release_id"),
        "release_status": manifest.get("release_status"),
        "chunk_count": actual_count,
        "chunk_sha256": actual_hash,
        "errors": errors,
    }


def qdrant_gate_dependency() -> dict[str, Any]:
    """Probe the active Qdrant index without mutating it."""
    return evaluate_qdrant_gate()


def runtime_gate_dependency() -> dict[str, Any]:
    """Warm and cache dependencies used by the public retrieval methods."""
    return evaluate_cached_runtime_gate()


def release_gate_dependency(
    qdrant_report: dict[str, Any] = Depends(qdrant_gate_dependency),
    runtime_report: dict[str, Any] = Depends(runtime_gate_dependency),
) -> dict[str, Any]:
    """Combine immutable release, Qdrant, and functional runtime readiness."""
    report = evaluate_release_gate()
    report["qdrant"] = qdrant_report
    report["runtime"] = runtime_report
    report["errors"].extend(qdrant_report.get("errors", []))
    report["errors"].extend(runtime_report.get("errors", []))
    report["status"] = "ready" if not report["errors"] else "not_ready"
    return report


def require_authorized_release(
    report: dict[str, Any] = Depends(release_gate_dependency),
) -> None:
    """Block legal answers when the configured release fails closed."""
    if report["status"] != "ready":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "release_not_ready",
                "release_id": report.get("release_id"),
                "errors": report.get("errors", []),
            },
        )


@router.get("/ready")
async def readiness_check(
    report: dict[str, Any] = Depends(release_gate_dependency),
) -> JSONResponse:
    """Report corpus/authority readiness without changing process liveness."""
    return JSONResponse(
        status_code=200 if report["status"] == "ready" else 503,
        content=report,
    )
