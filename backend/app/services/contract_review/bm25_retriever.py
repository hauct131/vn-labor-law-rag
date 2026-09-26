from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

from .models import ContractSection
from .retriever import ClauseMatch


CATEGORY_QUERY_TERMS: dict[str, tuple[str, ...]] = {
    "probation": (
        "thử việc",
        "thời gian thử việc",
        "thời hạn thử việc",
        "lương thử việc",
        "kết thúc thử việc",
        "đánh giá thử việc",
    ),
    "salary": (
        "tiền lương",
        "mức lương",
        "lương theo công việc",
        "phụ cấp",
        "kỳ hạn trả lương",
        "ngày trả lương",
        "phương thức trả lương",
        "chậm trả lương",
        "khấu trừ",
    ),
    "working_time": (
        "thời giờ làm việc",
        "thời gian làm việc",
        "giờ làm việc",
        "lịch làm việc",
        "ca làm việc",
        "nghỉ giữa giờ",
        "nghỉ giữa ca",
        "nghỉ hằng tuần",
        "nghỉ hàng tuần",
        "làm thêm giờ",
    ),
    "termination": (
        "chấm dứt hợp đồng",
        "chấm dứt hợp đồng lao động",
        "hết hạn hợp đồng",
        "đơn phương chấm dứt",
        "thời hạn báo trước",
        "báo trước",
        "bàn giao khi chấm dứt",
    ),
}


def _ascii(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFD",
        value.casefold().replace("đ", "d"),
    )

    return "".join(
        char
        for char in normalized
        if unicodedata.category(char) != "Mn"
    )


def _words(value: str) -> list[str]:
    return re.findall(
        r"[a-z0-9]+",
        _ascii(value),
    )


def _phrase_token(value: str) -> str:
    return "_".join(_words(value))


def _document_tokens(value: str) -> list[str]:
    """
    Build lexical tokens for BM25.

    We keep:
    - unigrams for distinctive single-word concepts such as "luong"
      or "phu_cap"
    - bigrams/trigrams/four-grams so Vietnamese multi-word concepts
      such as "thu_viec" remain intact.

    This prevents "cong viec" from matching the query phrase
    "thu viec" merely because both contain the generic token "viec".
    """
    words = _words(value)

    tokens = list(words)

    for n in (2, 3, 4):
        tokens.extend(
            "_".join(words[index:index + n])
            for index in range(len(words) - n + 1)
        )

    return tokens


def _section_text(section: ContractSection) -> str:
    parts: list[str] = []

    if section.heading:
        parts.append(section.heading)

    parts.extend(
        block.text
        for block in section.blocks
        if block.text.strip()
    )

    return "\n".join(parts)


class BM25ClauseRetriever:
    """
    Phrase-aware BM25 retrieval over ContractSection objects.

    Each contract section is one BM25 document.

    Category semantics are represented by lexical query phrases.
    No heading boost or Legacy V1 scoring heuristic is used.
    """

    def __init__(
        self,
        *,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        if k1 <= 0:
            raise ValueError("k1 must be > 0")

        if not 0 <= b <= 1:
            raise ValueError(
                "b must be between 0 and 1"
            )

        self.k1 = k1
        self.b = b

    def retrieve(
        self,
        sections: list[ContractSection],
        category: str,
        top_k: int = 3,
    ) -> list[ClauseMatch]:
        query_phrases = CATEGORY_QUERY_TERMS.get(
            category
        )

        # Validate category before early returns.
        if query_phrases is None:
            raise ValueError(
                "Unsupported contract review category: "
                f"{category!r}"
            )

        if top_k <= 0 or not sections:
            return []

        documents = [
            Counter(
                _document_tokens(
                    _section_text(section)
                )
            )
            for section in sections
        ]

        lengths = [
            sum(document.values())
            for document in documents
        ]

        average_length = (
            sum(lengths) / len(lengths)
            if lengths
            else 0.0
        )

        document_frequency: Counter[str] = Counter()

        for document in documents:
            document_frequency.update(
                document.keys()
            )

        # Query uses complete semantic phrases rather than
        # all component words.
        query_terms = Counter(
            _phrase_token(phrase)
            for phrase in query_phrases
        )

        total_documents = len(documents)

        matches: list[ClauseMatch] = []

        for index, document in enumerate(documents):
            score = 0.0
            document_length = lengths[index]

            for term, query_frequency in query_terms.items():
                term_frequency = document.get(
                    term,
                    0,
                )

                if term_frequency == 0:
                    continue

                df = document_frequency[term]

                idf = math.log(
                    1.0
                    + (
                        total_documents
                        - df
                        + 0.5
                    )
                    / (
                        df
                        + 0.5
                    )
                )

                if average_length > 0:
                    length_normalization = (
                        1.0
                        - self.b
                        + self.b
                        * document_length
                        / average_length
                    )
                else:
                    length_normalization = 1.0

                denominator = (
                    term_frequency
                    + self.k1
                    * length_normalization
                )

                score += (
                    query_frequency
                    * idf
                    * (
                        term_frequency
                        * (self.k1 + 1.0)
                    )
                    / denominator
                )

            if score <= 0:
                continue

            matches.append(
                ClauseMatch(
                    section=sections[index],
                    score=score,
                )
            )

        matches.sort(
            key=lambda match: (
                -match.score,
                match.section.index,
            )
        )

        return matches[:top_k]
