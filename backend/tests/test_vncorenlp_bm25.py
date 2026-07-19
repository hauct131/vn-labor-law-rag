"""Unit tests for the VnCoreNLP-backed BM25 adapter."""

from __future__ import annotations

import pytest

from backend.app.retrieval.vncorenlp_bm25 import VnCoreNlpBm25


class FakeSegmenter:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def word_segment(self, text: str) -> list[str]:
        self.inputs.append(text)
        replacements = {
            "đình công": "đình_công",
            "bất hợp pháp": "bất_hợp_pháp",
            "trường hợp": "trường_hợp",
        }
        segmented = text
        for source, target in replacements.items():
            segmented = segmented.replace(source, target)
        return [segmented]


def build_model() -> tuple[VnCoreNlpBm25, FakeSegmenter]:
    segmenter = FakeSegmenter()
    model = VnCoreNlpBm25.from_documents(
        [
            "Trường hợp đình công bất hợp pháp.",
            "Xử lý đình công.",
        ],
        segmenter=segmenter,
    )
    return model, segmenter


def test_analysis_uses_vncorenlp_compound_words_and_unicode_normalization():
    model, segmenter = build_model()
    tokens = model.analyze(
        "Những TRƯỜNG HỢP đình công bất hợp pháp?"
    )
    assert segmenter.inputs[-1] == (
        "những trường hợp đình công bất hợp pháp?"
    )
    assert tokens == (
        "những",
        "trường_hợp",
        "đình_công",
        "bất_hợp_pháp",
    )


def test_vocabulary_is_deterministic_and_contains_no_punctuation():
    first, _ = build_model()
    second, _ = build_model()
    assert first.vocabulary == second.vocabulary
    assert list(first.vocabulary) == sorted(first.vocabulary)
    assert "." not in first.vocabulary


def test_query_embedding_uses_unit_weights_and_ignores_oov_tokens():
    model, _ = build_model()
    embedding = next(model.query_embed(
        ["Đình công và một từ hoàn toàn ngoài từ điển"]
    ))
    expected_id = model.vocabulary["đình_công"]
    assert embedding.indices == [expected_id]
    assert embedding.values == [1.0]


def test_document_embedding_applies_positive_bm25_tf_weights():
    model, _ = build_model()
    embedding = next(model.embed(["Đình công đình công."]))
    assert embedding.indices == [model.vocabulary["đình_công"]]
    assert len(embedding.values) == 1
    assert embedding.values[0] > 0


def test_metadata_records_segmenter_and_no_stopword_removal():
    model, _ = build_model()
    assert model.metadata["word_segmenter"] == (
        "VnCoreNLP-1.2/RDRSegmenter"
    )
    assert model.metadata["stopwords_removed"] is False
    assert model.metadata["vocabulary_size"] == len(model.vocabulary)


def test_empty_corpus_is_rejected():
    with pytest.raises(ValueError, match="documents must not be empty"):
        VnCoreNlpBm25.from_documents([], segmenter=FakeSegmenter())
