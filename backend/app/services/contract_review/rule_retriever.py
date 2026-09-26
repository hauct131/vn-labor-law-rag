import unicodedata

from .models import ContractSection
from .retriever import ClauseMatch


CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "probation": (
        "thử việc",
        "thời gian thử việc",
        "thời hạn thử việc",
        "lương thử việc",
        "mức lương thử việc",
    ),
    "salary": (
        "tiền lương",
        "mức lương",
        "lương theo công việc",
        "trả lương",
        "thanh toán lương",
        "thời điểm trả lương",
        "kỳ hạn trả lương",
    ),
    "working_time": (
        "thời giờ làm việc",
        "thời gian làm việc",
        "giờ làm việc",
        "ngày làm việc",
        "ca làm việc",
        "nghỉ hằng tuần",
        "nghỉ hàng tuần",
    ),
    "termination": (
        "chấm dứt hợp đồng",
        "chấm dứt hợp đồng lao động",
        "đơn phương chấm dứt",
        "thời hạn báo trước",
        "báo trước",
    ),
}


def _normalize(value: str) -> str:
    """
    Normalize Vietnamese text for deterministic lexical matching.

    Examples:
        "Thử việc" -> "thu viec"
        "Đơn phương" -> "don phuong"
    """
    normalized = unicodedata.normalize("NFD", value.casefold())

    without_marks = "".join(
        char
        for char in normalized
        if unicodedata.category(char) != "Mn"
    )

    return (
        without_marks
        .replace("đ", "d")
        .replace("Đ", "D")
    )


class RuleClauseRetriever:
    """
    Lexical baseline for contract-clause retrieval.

    This retriever intentionally relies on manually defined keywords.
    It does not perform semantic matching.

    It exists primarily as a deterministic baseline for comparison with:
    - BM25
    - dense E5 retrieval
    - hybrid BM25 + E5 retrieval
    """

    def retrieve(
        self,
        sections: list[ContractSection],
        category: str,
        top_k: int = 3,
    ) -> list[ClauseMatch]:
        if top_k <= 0:
            return []

        terms = CATEGORY_TERMS.get(category)

        if terms is None:
            raise ValueError(
                f"Unsupported contract review category: {category!r}"
            )

        normalized_terms = tuple(
            _normalize(term)
            for term in terms
        )

        matches: list[ClauseMatch] = []

        for section in sections:
            score = self._score_section(
                section=section,
                normalized_terms=normalized_terms,
            )

            if score <= 0:
                continue

            matches.append(
                ClauseMatch(
                    section=section,
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

    @staticmethod
    def _score_section(
        section: ContractSection,
        normalized_terms: tuple[str, ...],
    ) -> float:
        """
        Score a section using lexical keyword matches.

        Heading matches receive a larger weight because a heading such as
        "Điều 2. Thử việc" is a strong structural signal.

        Body matches still contribute so that sections without descriptive
        headings can be retrieved when their content contains expected terms.
        """
        heading = _normalize(section.heading or "")
        body = _normalize(
            "\n".join(block.text for block in section.blocks)
        )

        heading_hits = sum(
            1
            for term in normalized_terms
            if term in heading
        )

        body_hits = sum(
            1
            for term in normalized_terms
            if term in body
        )

        return float(
            heading_hits * 4
            + body_hits
        )