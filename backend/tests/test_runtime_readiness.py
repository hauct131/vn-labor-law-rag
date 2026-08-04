from __future__ import annotations

from types import SimpleNamespace

from app.core.runtime_readiness import (
    clear_runtime_readiness_cache,
    evaluate_cached_runtime_gate,
    evaluate_runtime_gate,
)


def make_settings(model_dir, **overrides):
    defaults = {
        "vncorenlp_model_dir": str(model_dir),
        "runtime_java_timeout_seconds": 3.0,
        "runtime_smoke_query": "quyền của người lao động",
        "runtime_readiness_ttl_seconds": 30.0,
        "runtime_readiness_failure_ttl_seconds": 5.0,
        "llm_provider": "openrouter",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_assets(model_dir) -> None:
    (model_dir / "models" / "wordsegmenter").mkdir(parents=True)
    (model_dir / "VnCoreNLP-1.2.jar").write_bytes(b"test")
    (
        model_dir
        / "models"
        / "wordsegmenter"
        / "wordsegmenter.rdr"
    ).write_bytes(b"test")


class FakeSparse:
    metadata = {
        "analyzer": "vncorenlp/rdrsegmenter-bm25-v1",
        "document_count": 833,
    }

    def retrieve(self, query, *, top_k):
        assert query == "quyền của người lao động"
        assert top_k == 1
        return [SimpleNamespace(chunk_id="chunk-1")]


class FakeDense:
    model_name = "intfloat/multilingual-e5-large"
    vector_size = 1024
    cache_dir = "/models/fastembed"

    def __init__(self):
        self.warmed = False

    def warmup(self):
        self.warmed = True


def successful_java_runner(*args, **kwargs):
    assert args[0][-1] == "-version"
    assert kwargs["timeout"] == 3.0
    return SimpleNamespace(
        returncode=0,
        stderr='openjdk version "17.0.15"',
        stdout="",
    )


def test_runtime_gate_warms_all_functional_dependencies(tmp_path) -> None:
    model_dir = tmp_path / "vncorenlp"
    make_assets(model_dir)
    dense = FakeDense()
    methods = []

    def retriever_provider(method):
        methods.append(method)
        return FakeSparse() if method == "sparse" else dense

    report = evaluate_runtime_gate(
        make_settings(model_dir),
        retriever_provider=retriever_provider,
        generation_factory=lambda: SimpleNamespace(
            model="openrouter/free"
        ),
        java_locator=lambda _: "/usr/bin/java",
        java_runner=successful_java_runner,
    )

    assert report["status"] == "ready"
    assert report["errors"] == []
    assert methods == ["sparse", "dense"]
    assert dense.warmed is True
    assert (
        report["components"]["sparse_retriever"]["document_count"]
        == 833
    )
    assert (
        report["components"]["dense_retriever"]["vector_size"]
        == 1024
    )
    assert report["components"]["generation"]["network_probe"] is False


def test_runtime_gate_reports_missing_assets_without_starting_sparse(
    tmp_path,
) -> None:
    calls = []

    def retriever_provider(method):
        calls.append(method)
        if method == "dense":
            raise RuntimeError("dense model is not cached")
        raise AssertionError("sparse must be skipped")

    def generation_factory():
        raise RuntimeError("missing OPENROUTER_API_KEY")

    report = evaluate_runtime_gate(
        make_settings(tmp_path / "missing"),
        retriever_provider=retriever_provider,
        generation_factory=generation_factory,
        java_locator=lambda _: None,
    )

    assert report["status"] == "not_ready"
    assert calls == ["dense"]
    assert report["errors"] == [
        "runtime_java_missing",
        "runtime_vncorenlp_jar_missing",
        "runtime_vncorenlp_models_missing",
        "runtime_sparse_probe_skipped",
        "runtime_dense_probe_failed",
        "runtime_generation_config_invalid",
    ]
    assert "API_KEY" in report["components"]["generation"]["error"]


def test_runtime_gate_cache_reuses_and_copies_report(tmp_path) -> None:
    clear_runtime_readiness_cache()
    calls = 0
    settings_obj = make_settings(tmp_path, runtime_readiness_ttl_seconds=60)

    def evaluator():
        nonlocal calls
        calls += 1
        return {
            "status": "ready",
            "components": {"dense_retriever": {"status": "ready"}},
            "errors": [],
        }

    first = evaluate_cached_runtime_gate(
        settings_obj,
        evaluator=evaluator,
    )
    first["errors"].append("caller_mutation")
    second = evaluate_cached_runtime_gate(
        settings_obj,
        evaluator=evaluator,
    )
    clear_runtime_readiness_cache()

    assert calls == 1
    assert second["errors"] == []
