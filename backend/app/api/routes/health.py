import hashlib
import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Process liveness only; use /ready for corpus readiness."""
    return HealthResponse(status="ok")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@router.get("/ready")
async def readiness_check() -> JSONResponse:
    """Fail closed when the configured release is missing or unapproved."""
    chunks_path = Path(settings.legal_chunks_path)
    manifest_path = Path(settings.corpus_release_manifest_path)
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
        if actual_count != settings.retrieval_expected_chunks:
            errors.append(
                "chunk_count_mismatch:"
                f"{actual_count}!={settings.retrieval_expected_chunks}"
            )
        if actual_hash != settings.retrieval_corpus_sha256:
            errors.append("chunk_hash_mismatch")

    if manifest:
        if manifest.get("release_id") != settings.corpus_release_id:
            errors.append("release_id_mismatch")
        manifest_chunk_hash = (
            manifest.get("hashes", {}).get("chunks_sha256")
        )
        if actual_hash and manifest_chunk_hash != actual_hash:
            errors.append("manifest_chunk_hash_mismatch")
        if (
            settings.corpus_require_authority_approval
            and not manifest.get("gates", {}).get(
                "authority_review_passed", False
            )
        ):
            errors.append("authority_review_pending")

    ready = not errors
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "release_id": manifest.get("release_id"),
            "release_status": manifest.get("release_status"),
            "chunk_count": actual_count,
            "chunk_sha256": actual_hash,
            "errors": errors,
        },
    )
