"""Validation and atomic file helpers for the annotation panel."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from evaluation.answer_quality.annotations import AnnotationPayload
from evaluation.answer_quality.schema import (
    AnswerQualityDataset,
    AnswerQualityQuestion,
)


class PanelConfigurationError(ValueError):
    """Raised before any provider request when panel configuration is unsafe."""


class AnnotationReferenceError(ValueError):
    """Raised when model output cites evidence outside the supplied packet."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def validate_models(models: list[str]) -> list[str]:
    normalized = [model.strip() for model in models]
    if len(normalized) < 3:
        raise PanelConfigurationError(
            "at least three explicit model IDs are required"
        )
    if any(not model for model in normalized):
        raise PanelConfigurationError("model IDs must not be blank")
    if len(set(normalized)) != len(normalized):
        raise PanelConfigurationError("model IDs must be unique")
    if any("/" not in model for model in normalized):
        raise PanelConfigurationError(
            "every model ID must include its OpenRouter organization prefix"
        )
    organizations = [model.split("/", 1)[0].casefold() for model in normalized]
    if len(set(organizations)) != len(organizations):
        raise PanelConfigurationError(
            "panel models must come from distinct model organizations"
        )
    return normalized


def resolve_corpus_path(
    dataset: AnswerQualityDataset,
    repo_root: Path,
) -> Path:
    path = Path(dataset.source_corpus_path)
    if path.is_absolute():
        raise PanelConfigurationError("dataset corpus path must be relative")
    resolved = (repo_root / path).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise PanelConfigurationError(
            "dataset corpus path escapes repository"
        ) from exc
    if not resolved.is_file():
        raise PanelConfigurationError(f"corpus file is missing: {resolved}")
    return resolved


def validate_annotation_references(
    question: AnswerQualityQuestion,
    annotation: AnnotationPayload,
) -> None:
    allowed_articles = set(question.expected_article_codes)
    allowed_chunks = set(question.evidence_chunk_ids)
    for claim in annotation.required_claims:
        articles = set(claim.supported_by_article_codes)
        chunks = set(claim.supported_by_chunk_ids)
        if not articles.issubset(allowed_articles):
            raise AnnotationReferenceError(
                f"claim {claim.claim_id} cites an article outside evidence"
            )
        if not chunks.issubset(allowed_chunks):
            raise AnnotationReferenceError(
                f"claim {claim.claim_id} cites a chunk outside evidence"
            )


def record_name(model_index: int, model: str) -> str:
    suffix = hashlib.sha256(model.encode("utf-8")).hexdigest()[:12]
    return f"{model_index:02d}-{suffix}.json"
