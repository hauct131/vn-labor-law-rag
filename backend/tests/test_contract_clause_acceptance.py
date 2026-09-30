import pytest

from app.services.contract_review.bm25_retriever import (
    BM25ClauseRetriever,
)
from app.services.contract_review.clause_acceptance import (
    ClauseAcceptanceGate,
)
from app.services.contract_review.e5_retriever import (
    E5ClauseRetriever,
)
from app.services.contract_review.hybrid_retriever import (
    HybridClauseRetriever,
)
from app.services.contract_review.segmenter import (
    segment_contract,
)
from app.services.contract_review_service import (
    CATEGORIES,
    _excerpt_for,
    _paragraphs,
)


@pytest.mark.parametrize(
    ("category", "text", "expected_fragment"),
    (
        (
            "probation",
            """
            Điều 1. Giai đoạn ban đầu
            Trong 60 ngày đầu kể từ ngày nhận việc, kết quả thực hiện nhiệm vụ
            của người lao động sẽ được doanh nghiệp xem xét trước khi tiếp tục
            bố trí chính thức.

            Điều 2. Công việc
            Người lao động thực hiện nhiệm vụ phát triển hệ thống nội bộ.
            """,
            "60 ngày đầu",
        ),
        (
            "working_time",
            """
            Điều 1. Tổ chức công việc
            Người lao động có mặt từ 08:00 đến 17:00 từ thứ Hai đến thứ Sáu
            và nghỉ trưa 60 phút.

            Điều 2. Công việc
            Người lao động thực hiện nhiệm vụ phát triển hệ thống nội bộ.
            """,
            "08:00 đến 17:00",
        ),
        (
            "termination",
            """
            Điều 1. Kết thúc quan hệ
            Nếu một bên muốn kết thúc quan hệ lao động trước ngày hết hạn,
            bên đó phải thông báo bằng văn bản cho bên kia ít nhất 30 ngày.

            Điều 2. Công việc
            Người lao động thực hiện nhiệm vụ phát triển hệ thống nội bộ.
            """,
            "ít nhất 30 ngày",
        ),
    ),
)
def test_semantic_only_clause_can_survive_acceptance_gate(
    category: str,
    text: str,
    expected_fragment: str,
) -> None:
    paragraphs = _paragraphs(text)
    sections = segment_contract(paragraphs)

    rule = next(
        rule
        for rule in CATEGORIES
        if rule.key == category
    )

    # Guard the purpose of this regression:
    # the old selector must NOT be able to solve this case.
    assert _excerpt_for(
        rule,
        paragraphs,
    ) == ""

    bm25 = BM25ClauseRetriever()

    # The lexical BM25 retriever must also miss it.
    assert bm25.retrieve(
        sections=sections,
        category=category,
        top_k=len(sections),
    ) == []

    e5 = E5ClauseRetriever()

    e5.prepare(sections)
    e5.prepare_queries(
        rule.key
        for rule in CATEGORIES
    )

    hybrid = HybridClauseRetriever(
        bm25=bm25,
        e5=e5,
        rrf_k=60,
        bm25_weight=1.0,
        e5_weight=1.0,
    )

    gate = ClauseAcceptanceGate(
        bm25=bm25,
        e5=e5,
    )

    matches = hybrid.retrieve(
        sections=sections,
        category=category,
        top_k=3,
    )

    assert matches

    accepted = None
    acceptance = None

    for candidate in matches:
        result = gate.evaluate(
            sections=sections,
            category=category,
            candidate=candidate,
        )

        if result.accepted:
            accepted = candidate
            acceptance = result
            break

    assert accepted is not None
    assert acceptance is not None

    assert (
        acceptance.reason
        == "semantic_category_margin"
    )

    assert not acceptance.bm25_supported

    assert (
        acceptance.semantic_margin
        is not None
    )

    assert (
        acceptance.semantic_margin
        >= 0.01
    )

    assert (
        expected_fragment
        in accepted.section.text
    )


def test_semantic_only_ambiguous_salary_clause_is_rejected() -> None:
    text = """
    Điều 1. Quyền lợi
    Khoản thù lao cố định cho vị trí này là 18.000.000 đồng mỗi tháng
    và được chuyển khoản vào ngày 05 của tháng kế tiếp.

    Điều 2. Công việc
    Người lao động thực hiện nhiệm vụ phát triển hệ thống nội bộ.
    """

    paragraphs = _paragraphs(text)
    sections = segment_contract(paragraphs)

    rule = next(
        rule
        for rule in CATEGORIES
        if rule.key == "salary"
    )

    # Legacy selector cannot solve this paraphrase.
    assert _excerpt_for(
        rule,
        paragraphs,
    ) == ""

    bm25 = BM25ClauseRetriever()

    # BM25 also has no lexical match.
    assert bm25.retrieve(
        sections=sections,
        category="salary",
        top_k=len(sections),
    ) == []

    e5 = E5ClauseRetriever()

    e5.prepare(sections)
    e5.prepare_queries(
        rule.key
        for rule in CATEGORIES
    )

    hybrid = HybridClauseRetriever(
        bm25=bm25,
        e5=e5,
        rrf_k=60,
        bm25_weight=1.0,
        e5_weight=1.0,
    )

    gate = ClauseAcceptanceGate(
        bm25=bm25,
        e5=e5,
    )

    matches = hybrid.retrieve(
        sections=sections,
        category="salary",
        top_k=3,
    )

    assert matches

    candidate = matches[0]

    assert (
        "Khoản thù lao cố định"
        in candidate.section.text
    )

    result = gate.evaluate(
        sections=sections,
        category="salary",
        candidate=candidate,
    )

    assert result.accepted is False
    assert (
        result.reason
        == "semantic_category_ambiguous"
    )
    assert result.bm25_supported is False

    assert (
        result.requested_e5_score
        is not None
    )
    assert (
        result.competing_e5_score
        is not None
    )
    assert (
        result.semantic_margin
        is not None
    )

    assert result.semantic_margin < 0