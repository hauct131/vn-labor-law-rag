from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import app.core.paths as project_paths
from app.core.runtime_readiness import evaluate_runtime_gate
from app.retrieval.sparse_retriever import load_legal_chunks
from app.services.official_sources import OfficialSourceRegistry
from app.services.source_catalog import LegalSourceCatalog


def _set_project_root(monkeypatch, tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr(project_paths, "PROJECT_ROOT", project_root)
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    return project_root


def test_resolve_project_path_is_independent_of_cwd(monkeypatch, tmp_path) -> None:
    project_root = _set_project_root(monkeypatch, tmp_path)

    resolved = project_paths.resolve_project_path("data/example.json")

    assert resolved == (project_root / "data/example.json").resolve()


def test_runtime_consumers_resolve_relative_paths_from_project_root(
    monkeypatch, tmp_path
) -> None:
    project_root = _set_project_root(monkeypatch, tmp_path)

    registry_path = project_root / "data/reference/official.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps({
            "documents": {
                "vbpl:item:1": {
                    "item_id": "1",
                    "document_number": "01/2026/TEST",
                    "title": "Văn bản kiểm thử",
                    "canonical_url": "https://example.com/canonical",
                    "original_url": "https://example.com/original",
                }
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    registry = OfficialSourceRegistry.from_path(
        "data/reference/official.json"
    )
    assert registry.records[0].document_number == "01/2026/TEST"

    chunks_path = project_root / "data/releases/test/chunks.jsonl"
    chunks_path.parent.mkdir(parents=True)
    chunk = {
        "chunk_id": "chunk-1",
        "article_code": "20.2.TEST.1",
        "content": "Điều 1\n\nNội dung kiểm thử.",
    }
    chunks_path.write_text(
        json.dumps(chunk, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    chunks, _ = load_legal_chunks("data/releases/test/chunks.jsonl")
    assert chunks[0]["chunk_id"] == "chunk-1"

    catalog = LegalSourceCatalog(
        chunks_path="data/releases/test/chunks.jsonl",
        official_sources=OfficialSourceRegistry.empty(),
    )
    assert catalog.get_article("20.2.TEST.1").chunk_count == 1


def test_runtime_gate_resolves_relative_vncorenlp_assets(
    monkeypatch, tmp_path
) -> None:
    project_root = _set_project_root(monkeypatch, tmp_path)
    model_dir = project_root / "models/vncorenlp"
    (model_dir / "models/wordsegmenter").mkdir(parents=True)
    (model_dir / "VnCoreNLP-1.2.jar").write_bytes(b"test")
    (model_dir / "models/wordsegmenter/model.bin").write_bytes(b"test")

    class Sparse:
        metadata = {"analyzer": "test", "document_count": 1}

        def retrieve(self, query, *, top_k):
            return [SimpleNamespace(chunk_id="chunk-1")]

    class Dense:
        model_name = "test"
        vector_size = 1024
        cache_dir = None

        def warmup(self):
            return None

    def retriever_provider(method):
        return Sparse() if method == "sparse" else Dense()

    settings = SimpleNamespace(
        vncorenlp_model_dir="models/vncorenlp",
        runtime_java_timeout_seconds=3.0,
        runtime_smoke_query="kiểm thử",
        llm_provider="openrouter",
    )
    report = evaluate_runtime_gate(
        settings,
        retriever_provider=retriever_provider,
        generation_factory=lambda: SimpleNamespace(model="test"),
        java_locator=lambda _: "/usr/bin/java",
        java_runner=lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stderr='openjdk version "17"', stdout=""
        ),
    )

    assert report["status"] == "ready"
    assert report["components"]["vncorenlp_assets"]["model_dir"] == str(
        model_dir.resolve()
    )
