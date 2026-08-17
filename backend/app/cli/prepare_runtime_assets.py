"""Download and verify local assets required by the production retrievers."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from app.core.config import settings


def _require_java() -> str:
    executable = shutil.which("java")
    if executable is None:
        raise RuntimeError(
            "Java was not found. Install a Java 8+ runtime before continuing."
        )
    completed = subprocess.run(
        [executable, "-version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=settings.runtime_java_timeout_seconds,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"java -version exited with code {completed.returncode}"
        )
    version = (
        completed.stderr.strip() or completed.stdout.strip()
    ).splitlines()
    print(f"java=ready version={version[0] if version else 'unknown'}")
    return executable


def _vncorenlp_ready(model_dir: Path) -> bool:
    jar_path = model_dir / "VnCoreNLP-1.2.jar"
    models_path = model_dir / "models"
    return (
        jar_path.is_file()
        and models_path.is_dir()
        and any(path.is_file() for path in models_path.rglob("*"))
    )


def prepare_vncorenlp(model_dir: Path) -> None:
    """Download missing official VnCoreNLP assets and run a real smoke test."""
    resolved = model_dir.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    try:
        import py_vncorenlp
    except ImportError as exc:
        raise RuntimeError(
            "py_vncorenlp is not installed; install backend requirements first"
        ) from exc

    if not _vncorenlp_ready(resolved):
        # py_vncorenlp 0.1.4 cannot resume an interrupted download because it
        # creates the directory tree before invoking wget. Remove only its
        # known incomplete outputs so a retry starts from a consistent state.
        jar_path = resolved / "VnCoreNLP-1.2.jar"
        models_path = resolved / "models"
        jar_path.unlink(missing_ok=True)
        if models_path.exists():
            shutil.rmtree(models_path)
        print(f"vncorenlp=downloading target={resolved}")
        py_vncorenlp.download_model(save_dir=str(resolved))
    if not _vncorenlp_ready(resolved):
        raise RuntimeError(
            "VnCoreNLP download did not create VnCoreNLP-1.2.jar and models/"
        )

    segmenter = py_vncorenlp.VnCoreNLP(
        annotators=["wseg"],
        save_dir=str(resolved),
    )
    sentences = segmenter.word_segment(
        "Người lao động có quyền nghỉ hằng năm."
    )
    if not sentences:
        raise RuntimeError("VnCoreNLP smoke segmentation returned no sentences")
    print(
        "vncorenlp=ready "
        f"model_dir={resolved} smoke={sentences[0]!r}"
    )


def prepare_dense_model(
    model_name: str,
    cache_dir: Path,
    vector_size: int,
    threads: int,
) -> None:
    """Materialize the FastEmbed model and verify its output dimension."""
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise RuntimeError(
            "fastembed is not installed; install backend requirements first"
        ) from exc

    resolved_cache = cache_dir.expanduser().resolve()
    resolved_cache.mkdir(parents=True, exist_ok=True)
    print(
        "dense_embedding=preparing "
        f"model={model_name} cache_dir={resolved_cache}"
    )
    model = TextEmbedding(
        model_name=model_name,
        cache_dir=str(resolved_cache),
        threads=threads,
    )
    try:
        vector = next(iter(model.embed([
            "query: quyền của người lao động",
        ])))
    except StopIteration as exc:
        raise RuntimeError(
            "FastEmbed smoke generation returned no vector"
        ) from exc
    actual_size = len(vector)
    if actual_size != vector_size:
        raise RuntimeError(
            "FastEmbed vector size mismatch: "
            f"expected {vector_size}, got {actual_size}"
        )
    print(
        "dense_embedding=ready "
        f"model={model_name} vector_size={actual_size}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download and verify VnCoreNLP and FastEmbed runtime assets."
        )
    )
    parser.add_argument(
        "--vncorenlp-dir",
        default=settings.vncorenlp_model_dir,
    )
    parser.add_argument(
        "--fastembed-cache-dir",
        default=settings.fastembed_cache_dir
        or str(Path.home() / ".cache" / "fastembed"),
    )
    parser.add_argument(
        "--dense-model",
        default=settings.dense_embedding_model,
    )
    parser.add_argument(
        "--dense-vector-size",
        type=int,
        default=settings.dense_vector_size,
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=settings.embedding_threads,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _require_java()
    prepare_vncorenlp(Path(args.vncorenlp_dir))
    prepare_dense_model(
        args.dense_model,
        Path(args.fastembed_cache_dir),
        args.dense_vector_size,
        args.threads,
    )
    print("RUNTIME_ASSETS_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
