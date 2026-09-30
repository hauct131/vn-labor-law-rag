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


def test_cr_p11_probation_salary_not_accepted_as_main_salary() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    # Original P11
    text = "Lương thử việc bằng 80% mức lương chính thức."
    review = review_contract(text, RetrievalMethod.SPARSE)
    by_category = {item.category: item for item in review.findings}

    assert by_category["probation"].severity == "warning"
    assert by_category["probation"].evidence_status == "supported"
    assert (
        by_category["probation"].contract_excerpt
        == "Lương thử việc bằng 80% mức lương chính thức."
    )

    assert by_category["salary"].evidence_status == "insufficient_evidence"
    assert (
        by_category["salary"].contract_excerpt
        == "Chưa tìm thấy điều khoản liên quan trong nội dung được trích xuất."
    )


def test_cr_p11_s1_probation_amount_only_insufficient_for_main_salary() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    text = "Lương thử việc là 12.000.000 đồng/tháng."
    review = review_contract(text, RetrievalMethod.SPARSE)
    by_category = {item.category: item for item in review.findings}

    assert by_category["probation"].evidence_status == "supported"
    assert by_category["salary"].evidence_status == "insufficient_evidence"


def test_cr_p11_s2_probation_amount_and_date_insufficient_for_main_salary() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    text = "Lương thử việc 12.000.000 đồng/tháng, trả vào ngày 10."
    review = review_contract(text, RetrievalMethod.SPARSE)
    by_category = {item.category: item for item in review.findings}

    assert by_category["probation"].evidence_status == "supported"
    assert by_category["salary"].evidence_status == "insufficient_evidence"


def test_cr_p11_s3_main_salary_and_probation_both_supported() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    text = "Thử việc 60 ngày. Mức lương chính thức là 15.000.000 đồng/tháng. Lương thử việc bằng 85%."
    review = review_contract(text, RetrievalMethod.SPARSE)
    by_category = {item.category: item for item in review.findings}

    assert by_category["probation"].evidence_status == "supported"
    assert by_category["salary"].evidence_status == "supported"


def test_cr_p11_s4_main_salary_only_supported() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    text = "Mức lương chính thức là 15.000.000 đồng/tháng."
    review = review_contract(text, RetrievalMethod.SPARSE)
    by_category = {item.category: item for item in review.findings}

    assert by_category["salary"].evidence_status == "supported"


def test_cr_p11_s5_main_salary_and_probation_ratio_both_supported() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    text = "Mức lương 15.000.000 đồng/tháng. Thử việc 60 ngày, lương thử việc bằng 85%."
    review = review_contract(text, RetrievalMethod.SPARSE)
    by_category = {item.category: item for item in review.findings}

    assert by_category["salary"].evidence_status == "supported"
    assert by_category["probation"].evidence_status == "supported"


def test_cr_p11_s6_official_and_probation_salary_same_sentence_both_supported() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    text = "Lương chính thức là 15.000.000 đồng/tháng, lương thử việc là 12.000.000 đồng/tháng."
    review = review_contract(text, RetrievalMethod.SPARSE)
    by_category = {item.category: item for item in review.findings}

    assert by_category["salary"].evidence_status == "supported"
    assert by_category["probation"].evidence_status == "supported"



def test_cr_p15_working_time_generalization_cases() -> None:
    from app.schemas.ask import RetrievalMethod
    from app.services.contract_review_service import review_contract

    # W1
    w1_text = "Lịch làm việc cụ thể sẽ được công ty bố trí sau."
    w1_review = review_contract(w1_text, RetrievalMethod.SPARSE)
    w1_wt = next(item for item in w1_review.findings if item.category == "working_time")
    assert w1_wt.severity == "attention"
    assert w1_wt.evidence_status == "supported"
    assert w1_wt.contract_excerpt == w1_text

    # W2
    w2_text = "Địa điểm làm việc sẽ được công ty bố trí sau."
    w2_review = review_contract(w2_text, RetrievalMethod.SPARSE)
    w2_wt = next(item for item in w2_review.findings if item.category == "working_time")
    assert w2_wt.evidence_status == "insufficient_evidence"

    # W3
    w3_text = "Thiết bị làm việc sẽ được bố trí sau."
    w3_review = review_contract(w3_text, RetrievalMethod.SPARSE)
    w3_wt = next(item for item in w3_review.findings if item.category == "working_time")
    assert w3_wt.evidence_status == "insufficient_evidence"

    # W4
    w4_text = "Người lao động làm việc 8 giờ/ngày. Chỗ ngồi sẽ được bố trí sau."
    w4_review = review_contract(w4_text, RetrievalMethod.SPARSE)
    w4_wt = next(item for item in w4_review.findings if item.category == "working_time")
    assert w4_wt.severity == "info"
    assert w4_wt.evidence_status == "supported"

    # U5
    u5_text = "Lịch làm việc chi tiết sẽ được hai bên thống nhất sau."
    u5_review = review_contract(u5_text, RetrievalMethod.SPARSE)
    u5_wt = next(item for item in u5_review.findings if item.category == "working_time")
    assert u5_wt.severity == "attention"
    assert u5_wt.evidence_status == "supported"

    # U6
    u6_text = "Thời giờ làm việc cụ thể sẽ được bổ sung sau."
    u6_review = review_contract(u6_text, RetrievalMethod.SPARSE)
    u6_wt = next(item for item in u6_review.findings if item.category == "working_time")
    assert u6_wt.severity == "attention"
    assert u6_wt.evidence_status == "supported"

    # U7
    u7_text = "Người lao động làm việc 8 giờ mỗi ngày, từ thứ Hai đến thứ Sáu."
    u7_review = review_contract(u7_text, RetrievalMethod.SPARSE)
    u7_wt = next(item for item in u7_review.findings if item.category == "working_time")
    assert u7_wt.severity == "info"
    assert u7_wt.evidence_status == "supported"