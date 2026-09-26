import pytest

from app.services.contract_review.bm25_retriever import (
    BM25ClauseRetriever,
)
from app.services.contract_review.segmenter import (
    segment_contract,
)


def test_bm25_ranks_salary_section_first():
    sections = segment_contract(
        [
            "Điều 1. Công việc",
            "Người lao động thực hiện công việc chuyên viên dữ liệu.",
            "Điều 2. Quyền lợi",
            "Người lao động được hưởng các chế độ của công ty.",
            "Điều 3. Tiền lương",
            (
                "Mức lương theo công việc là "
                "18.000.000 đồng/tháng."
            ),
            (
                "Tiền lương được trả vào ngày 05 "
                "hằng tháng qua tài khoản ngân hàng."
            ),
        ]
    )

    matches = BM25ClauseRetriever().retrieve(
        sections,
        "salary",
        top_k=3,
    )

    assert matches
    assert (
        matches[0].section.heading
        == "Điều 3. Tiền lương"
    )


def test_bm25_ranks_working_time_section_first():
    sections = segment_contract(
        [
            "Điều 1. Công việc",
            "Người lao động thực hiện công việc vận hành.",
            "Điều 4. Thời giờ làm việc",
            (
                "Thời giờ làm việc là 08 giờ/ngày, "
                "05 ngày/tuần."
            ),
            (
                "Người lao động được nghỉ hằng tuần "
                "và nghỉ giữa ca."
            ),
            "Điều 5. Quyền lợi",
            "Người lao động được hưởng bảo hiểm.",
        ]
    )

    matches = BM25ClauseRetriever().retrieve(
        sections,
        "working_time",
        top_k=1,
    )

    assert len(matches) == 1
    assert (
        matches[0].section.heading
        == "Điều 4. Thời giờ làm việc"
    )


def test_bm25_results_are_sorted_by_score():
    sections = segment_contract(
        [
            "Điều 1. Quyền lợi",
            "Kỳ hạn trả lương được thực hiện hằng tháng.",
            "Điều 2. Tiền lương",
            (
                "Mức lương theo công việc là "
                "18.000.000 đồng/tháng."
            ),
            (
                "Ngày trả lương là ngày 05 hằng tháng."
            ),
        ]
    )

    matches = BM25ClauseRetriever().retrieve(
        sections,
        "salary",
        top_k=2,
    )

    assert len(matches) == 2
    assert matches[0].score >= matches[1].score


def test_bm25_returns_empty_for_no_overlap():
    sections = segment_contract(
        [
            "Điều 1. Công việc",
            (
                "Người lao động thực hiện phân tích "
                "và tổng hợp dữ liệu."
            ),
        ]
    )

    matches = BM25ClauseRetriever().retrieve(
        sections,
        "probation",
        top_k=3,
    )

    assert matches == []


def test_bm25_top_k_zero_returns_empty():
    sections = segment_contract(
        [
            "Điều 3. Tiền lương",
            "Mức lương là 18.000.000 đồng/tháng.",
        ]
    )

    matches = BM25ClauseRetriever().retrieve(
        sections,
        "salary",
        top_k=0,
    )

    assert matches == []


def test_bm25_rejects_unknown_category():
    retriever = BM25ClauseRetriever()

    with pytest.raises(
        ValueError,
        match="Unsupported contract review category",
    ):
        retriever.retrieve(
            [],
            "unknown",
            top_k=3,
        )


def test_bm25_validates_parameters():
    with pytest.raises(ValueError):
        BM25ClauseRetriever(k1=0)

    with pytest.raises(ValueError):
        BM25ClauseRetriever(b=1.5)
