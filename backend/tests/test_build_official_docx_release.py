from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.build_official_docx_release import (
    DEFAULT_INDEXER_NEAR_LIMIT_TOKENS,
    DEFAULT_MODEL_MAX_TOKENS,
    DEFAULT_OPERATIONAL_MAX_TOKENS,
    DEFAULT_TARGET_TOKENS,
    E5PassageTokenCounter,
    INTER_ARTICLE_HEADING_RE,
    find_inter_article_heading_leaks,
    strip_inter_article_headings,
)


class _FakeTokenizer:
    def encode(self, text: str, *, add_special_tokens: bool):
        assert add_special_tokens is True
        return SimpleNamespace(ids=text.split())


def test_default_chunk_limits_preserve_indexer_headroom() -> None:
    assert 0 < DEFAULT_TARGET_TOKENS <= DEFAULT_OPERATIONAL_MAX_TOKENS
    assert (
        DEFAULT_OPERATIONAL_MAX_TOKENS
        < DEFAULT_INDEXER_NEAR_LIMIT_TOKENS
        < DEFAULT_MODEL_MAX_TOKENS
    )


def test_strip_chapter_heading_and_following_title() -> None:
    lines = [
        "1. Nội dung cuối của điều.",
        "Chương III",
        "ĐIỀU KHOẢN THI HÀNH",
    ]

    assert strip_inter_article_headings(lines) == [
        "1. Nội dung cuối của điều."
    ]


def test_strip_section_heading_and_following_title() -> None:
    lines = [
        "2. Nội dung cuối của điều.",
        "Mục 4",
        "ĐỐI THOẠI TẠI NƠI LÀM VIỆC",
    ]

    assert strip_inter_article_headings(lines) == [
        "2. Nội dung cuối của điều."
    ]


@pytest.mark.parametrize(
    "line",
    ["Chương II", "CHƯƠNG XVII", "Mục 1", "mục IV."],
)
def test_inter_article_heading_pattern_accepts_real_headings(line: str) -> None:
    assert INTER_ARTICLE_HEADING_RE.fullmatch(line)


def test_e5_counter_includes_passage_prefix() -> None:
    counter = E5PassageTokenCounter(
        _FakeTokenizer(),
        "intfloat/multilingual-e5-large",
    )

    assert counter.count("nội dung") == 3
    assert counter.name.endswith(":document")


def test_e5_counter_rejects_invalid_input() -> None:
    counter = E5PassageTokenCounter(
        _FakeTokenizer(),
        "intfloat/multilingual-e5-large",
    )

    with pytest.raises(TypeError):
        counter.count(None)  # type: ignore[arg-type]


def test_find_inter_article_heading_leaks() -> None:
    articles = [
        {
            "article_code": "20.2.LQ.8",
            "content_units": [
                {
                    "unit_id": "article-8-clause-7",
                    "text": "7. Nội dung\nChương II\nVIỆC LÀM",
                }
            ],
        }
    ]

    assert find_inter_article_heading_leaks(articles) == [
        {
            "article_code": "20.2.LQ.8",
            "unit_id": "article-8-clause-7",
            "heading": "Chương II",
        }
    ]
