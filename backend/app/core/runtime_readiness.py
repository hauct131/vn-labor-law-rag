"""Cached functional probes for question-answering runtime dependencies."""

from __future__ import annotations

import copy
import shutil
import subprocess
import time
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from app.chains.generation_chain import create_generation_chain
from app.core.config import settings
from app.retrieval.retriever_factory import get_retriever


RetrieverProvider = Callable[[object], Any]
GenerationFactory = Callable[[], Any]
JavaLocator = Callable[[str], str | None]
JavaRunner = Callable[..., Any]

_CACHE_LOCK = RLock()
_CACHED_REPORT: dict[str, Any] | None = None
_CACHE_EXPIRES_AT = 0.0


def _safe_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:500] or type(exc).__name__


def _component_failure(
    components: dict[str, Any],
    errors: list[str],
    component: str,
    code: str,
    detail: str | None = None,
) -> None:
    item: dict[str, Any] = {"status": "not_ready"}
    if detail:
        item["error"] = detail
    components[component] = item
    errors.append(code)


def evaluate_runtime_gate(
    settings_obj: Any = settings,
    *,
    retriever_provider: RetrieverProvider = get_retriever,
    generation_factory: GenerationFactory = create_generation_chain,
    java_locator: JavaLocator = shutil.which,
    java_runner: JavaRunner = subprocess.run,
) -> dict[str, Any]:
    """Warm the real retrievers and validate generation without LLM quota."""
    errors: list[str] = []
    components: dict[str, Any] = {}
    report: dict[str, Any] = {
        "status": "not_ready",
        "components": components,
        "errors": errors,
    }

    model_dir = Path(settings_obj.vncorenlp_model_dir).expanduser().resolve()
    jar_path = model_dir / "VnCoreNLP-1.2.jar"
    models_path = model_dir / "models"
    java_path = java_locator("java")

    if java_path is None:
        _component_failure(
            components,
            errors,
            "java",
            "runtime_java_missing",
        )
    else:
        try:
            completed = java_runner(
                [java_path, "-version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=settings_obj.runtime_java_timeout_seconds,
            )
            version = (
                completed.stderr.strip() or completed.stdout.strip()
            ).splitlines()
            if completed.returncode != 0:
                raise RuntimeError(
                    f"java -version exited {completed.returncode}"
                )
            components["java"] = {
                "status": "ready",
                "executable": java_path,
                "version": version[0] if version else "unknown",
            }
        except Exception as exc:
            _component_failure(
                components,
                errors,
                "java",
                "runtime_java_probe_failed",
                _safe_error(exc),
            )

    asset_errors: list[str] = []
    if not jar_path.is_file():
        asset_errors.append("missing_jar")
        errors.append("runtime_vncorenlp_jar_missing")
    if not models_path.is_dir() or not any(models_path.rglob("*")):
        asset_errors.append("missing_models")
        errors.append("runtime_vncorenlp_models_missing")
    components["vncorenlp_assets"] = {
        "status": "ready" if not asset_errors else "not_ready",
        "model_dir": str(model_dir),
        "jar_path": str(jar_path),
        "models_path": str(models_path),
        "errors": asset_errors,
    }

    can_probe_sparse = (
        components["java"]["status"] == "ready"
        and not asset_errors
    )
    if can_probe_sparse:
        try:
            sparse = retriever_provider("sparse")
            hits = sparse.retrieve(
                settings_obj.runtime_smoke_query,
                top_k=1,
            )
            if not hits:
                raise RuntimeError("sparse smoke query returned no hits")
            metadata = getattr(sparse, "metadata", {})
            components["sparse_retriever"] = {
                "status": "ready",
                "analyzer": metadata.get("analyzer"),
                "document_count": metadata.get("document_count"),
                "smoke_hit": getattr(hits[0], "chunk_id", None),
            }
        except Exception as exc:
            _component_failure(
                components,
                errors,
                "sparse_retriever",
                "runtime_sparse_probe_failed",
                _safe_error(exc),
            )
    else:
        _component_failure(
            components,
            errors,
            "sparse_retriever",
            "runtime_sparse_probe_skipped",
            "Java or VnCoreNLP assets are not ready",
        )

    try:
        dense = retriever_provider("dense")
        warmup = getattr(dense, "warmup", None)
        if not callable(warmup):
            raise RuntimeError("dense retriever has no warmup method")
        warmup()
        components["dense_retriever"] = {
            "status": "ready",
            "model": getattr(dense, "model_name", None),
            "vector_size": getattr(dense, "vector_size", None),
            "cache_dir": getattr(dense, "cache_dir", None),
        }
    except Exception as exc:
        _component_failure(
            components,
            errors,
            "dense_retriever",
            "runtime_dense_probe_failed",
            _safe_error(exc),
        )

    try:
        generator = generation_factory()
        components["generation"] = {
            "status": "ready",
            "provider": settings_obj.llm_provider,
            "model": getattr(generator, "model", None),
            "network_probe": False,
        }
    except Exception as exc:
        _component_failure(
            components,
            errors,
            "generation",
            "runtime_generation_config_invalid",
            _safe_error(exc),
        )

    if not errors:
        report["status"] = "ready"
    return report


def clear_runtime_readiness_cache() -> None:
    """Discard the process-level runtime readiness result."""
    global _CACHED_REPORT, _CACHE_EXPIRES_AT
    with _CACHE_LOCK:
        _CACHED_REPORT = None
        _CACHE_EXPIRES_AT = 0.0


def evaluate_cached_runtime_gate(
    settings_obj: Any = settings,
    *,
    evaluator: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Cache successful probes longer than failures to avoid hot-path work."""
    global _CACHED_REPORT, _CACHE_EXPIRES_AT
    with _CACHE_LOCK:
        now = time.monotonic()
        if _CACHED_REPORT is not None and now < _CACHE_EXPIRES_AT:
            return copy.deepcopy(_CACHED_REPORT)

        report = (
            evaluator()
            if evaluator is not None
            else evaluate_runtime_gate(settings_obj)
        )
        ttl = (
            settings_obj.runtime_readiness_ttl_seconds
            if report.get("status") == "ready"
            else settings_obj.runtime_readiness_failure_ttl_seconds
        )
        _CACHED_REPORT = copy.deepcopy(report)
        _CACHE_EXPIRES_AT = now + max(float(ttl), 0.0)
        return copy.deepcopy(report)
