"""Regression tests for preparing external runtime assets."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from app.cli.prepare_runtime_assets import prepare_vncorenlp


class _FakeSegmenter:
    def __init__(self, **_: object) -> None:
        pass

    def word_segment(self, text: str) -> list[str]:
        assert text
        return ["Người_lao_động có quyền nghỉ hằng_năm ."]


def test_prepare_vncorenlp_recovers_from_incomplete_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model_dir = tmp_path / "vncorenlp"
    stale_model = model_dir / "models" / "wordsegmenter" / "partial"
    stale_model.parent.mkdir(parents=True)
    stale_model.write_text("incomplete", encoding="utf-8")

    def fake_download_model(*, save_dir: str) -> None:
        target = Path(save_dir)
        assert not (target / "models").exists()
        assert not (target / "VnCoreNLP-1.2.jar").exists()
        (target / "VnCoreNLP-1.2.jar").write_bytes(b"fake-jar")
        model_file = target / "models" / "wordsegmenter" / "vi-vocab"
        model_file.parent.mkdir(parents=True)
        model_file.write_text("fake-model", encoding="utf-8")

    fake_module = SimpleNamespace(
        download_model=fake_download_model,
        VnCoreNLP=_FakeSegmenter,
    )
    monkeypatch.setitem(sys.modules, "py_vncorenlp", fake_module)

    prepare_vncorenlp(model_dir)

    assert (model_dir / "VnCoreNLP-1.2.jar").is_file()
    assert (model_dir / "models" / "wordsegmenter" / "vi-vocab").is_file()
    assert not stale_model.exists()


def test_backend_image_installs_vncorenlp_downloader() -> None:
    dockerfile = (
        Path(__file__).resolve().parents[1] / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "default-jdk-headless" in dockerfile
    assert "wget" in dockerfile
