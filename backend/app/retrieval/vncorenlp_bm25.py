"""BM25 sparse features backed by VnCoreNLP word segmentation.

VnCoreNLP owns the Vietnamese linguistic analysis.  This module only adapts
its word-segmented output to Qdrant's sparse-vector interface: it builds a
deterministic corpus vocabulary, applies the standard BM25 term-frequency
normalisation, and leaves inverse document frequency to Qdrant's IDF modifier.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Protocol, Sequence


MODEL_NAME = "vncorenlp/rdrsegmenter-bm25-v1"
_TOKEN_RE = re.compile(r"[^\W_]+(?:_[^\W_]+)*", flags=re.UNICODE)


class WordSegmenter(Protocol):
    """Minimum VnCoreNLP interface used by the sparse encoder."""

    def word_segment(self, text: str) -> list[str]: ...


@dataclass(frozen=True)
class SparseEmbedding:
    """FastEmbed-compatible sparse embedding value object."""

    indices: list[int]
    values: list[float]


def load_segmenter(model_dir: Path) -> WordSegmenter:
    """Load the official VnCoreNLP word segmenter from a local model folder."""
    resolved = model_dir.expanduser().resolve()
    jar_path = resolved / "VnCoreNLP-1.2.jar"
    models_path = resolved / "models"
    if not jar_path.is_file() or not models_path.is_dir():
        raise RuntimeError(
            "VnCoreNLP model directory must contain VnCoreNLP-1.2.jar and "
            f"models/: {resolved}"
        )
    try:
        import py_vncorenlp
    except ImportError as exc:
        raise RuntimeError(
            "py_vncorenlp is required for the VnCoreNLP BM25 probe"
        ) from exc
    return py_vncorenlp.VnCoreNLP(
        annotators=["wseg"],
        save_dir=str(resolved),
    )


class VnCoreNlpBm25:
    """Deterministic BM25 encoder over VnCoreNLP word tokens."""

    model_name = MODEL_NAME

    def __init__(
        self,
        *,
        segmenter: WordSegmenter,
        vocabulary: dict[str, int],
        avg_len: float,
        k: float = 1.2,
        b: float = 0.75,
        analysis_cache: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        if not vocabulary:
            raise ValueError("vocabulary must not be empty")
        if avg_len <= 0:
            raise ValueError("avg_len must be greater than zero")
        if k <= 0:
            raise ValueError("k must be greater than zero")
        if not 0 <= b <= 1:
            raise ValueError("b must be between zero and one")
        self.segmenter = segmenter
        self.vocabulary = dict(vocabulary)
        self.avg_len = float(avg_len)
        self.k = float(k)
        self.b = float(b)
        self._analysis_cache = analysis_cache or {}

    @classmethod
    def from_documents(
        cls,
        documents: Sequence[str],
        *,
        segmenter: WordSegmenter,
        k: float = 1.2,
        b: float = 0.75,
    ) -> "VnCoreNlpBm25":
        if not documents:
            raise ValueError("documents must not be empty")

        cache: dict[str, tuple[str, ...]] = {}
        all_tokens: set[str] = set()
        lengths: list[int] = []
        for document in documents:
            tokens = cls._segment(segmenter, document)
            cache[document] = tokens
            all_tokens.update(tokens)
            lengths.append(len(tokens))
        vocabulary = {
            token: index
            for index, token in enumerate(sorted(all_tokens))
        }
        return cls(
            segmenter=segmenter,
            vocabulary=vocabulary,
            avg_len=max(sum(lengths) / len(lengths), 1.0),
            k=k,
            b=b,
            analysis_cache=cache,
        )

    @classmethod
    def from_model_dir(
        cls,
        documents: Sequence[str],
        model_dir: Path,
        *,
        k: float = 1.2,
        b: float = 0.75,
    ) -> "VnCoreNlpBm25":
        return cls.from_documents(
            documents,
            segmenter=load_segmenter(model_dir),
            k=k,
            b=b,
        )

    @staticmethod
    def normalize(text: str) -> str:
        return unicodedata.normalize("NFC", text).casefold()

    @classmethod
    def _segment(
        cls,
        segmenter: WordSegmenter,
        text: str,
    ) -> tuple[str, ...]:
        sentences = segmenter.word_segment(cls.normalize(text))
        return tuple(
            token
            for sentence in sentences
            for raw_token in sentence.split()
            for token in _TOKEN_RE.findall(raw_token)
        )

    def analyze(self, text: str) -> tuple[str, ...]:
        cached = self._analysis_cache.get(text)
        if cached is not None:
            return cached
        tokens = self._segment(self.segmenter, text)
        self._analysis_cache[text] = tokens
        return tokens

    @property
    def metadata(self) -> dict[str, object]:
        vocabulary_bytes = "\n".join(
            sorted(self.vocabulary)
        ).encode("utf-8")
        return {
            "analyzer": MODEL_NAME,
            "word_segmenter": "VnCoreNLP-1.2/RDRSegmenter",
            "vocabulary_size": len(self.vocabulary),
            "vocabulary_sha256": hashlib.sha256(
                vocabulary_bytes
            ).hexdigest(),
            "average_document_length": round(self.avg_len, 6),
            "bm25_k": self.k,
            "bm25_b": self.b,
            "stopwords_removed": False,
        }

    def _document_embedding(self, text: str) -> SparseEmbedding:
        token_ids = [
            self.vocabulary[token]
            for token in self.analyze(text)
            if token in self.vocabulary
        ]
        counts = Counter(token_ids)
        document_length = max(len(token_ids), 1)
        length_norm = 1 - self.b + self.b * document_length / self.avg_len
        weighted: dict[int, float] = {}
        for token_id, frequency in counts.items():
            numerator = frequency * (self.k + 1)
            denominator = frequency + self.k * length_norm
            weighted[token_id] = numerator / denominator
        indices = sorted(weighted)
        return SparseEmbedding(
            indices=indices,
            values=[weighted[index] for index in indices],
        )

    def embed(
        self,
        documents: str | Iterable[str],
        batch_size: int = 256,
        **_: object,
    ) -> Iterator[SparseEmbedding]:
        del batch_size
        items = [documents] if isinstance(documents, str) else documents
        for document in items:
            yield self._document_embedding(document)

    def query_embed(
        self,
        queries: str | Iterable[str],
        **_: object,
    ) -> Iterator[SparseEmbedding]:
        items = [queries] if isinstance(queries, str) else queries
        for query in items:
            indices = sorted({
                self.vocabulary[token]
                for token in self.analyze(query)
                if token in self.vocabulary
            })
            yield SparseEmbedding(
                indices=indices,
                values=[1.0] * len(indices),
            )
