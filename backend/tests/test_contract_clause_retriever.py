from app.services.contract_review.rule_retriever import (
    RuleClauseRetriever,
)
from app.services.contract_review.segmenter import segment_contract


def test_rule_retriever_finds_probation_section():
    sections = segment_contract(
        [
            "Điều 1. Công việc",
            "Người lao động làm chuyên viên dữ liệu.",
            "Điều 2. Thử việc",
            "Thời gian thử việc là 60 ngày.",
            "Điều 3. Tiền lương",
            "Mức lương là 18.000.000 đồng/tháng.",
        ]
    )

    retriever = RuleClauseRetriever()

    matches = retriever.retrieve(
        sections=sections,
        category="probation",
        top_k=1,
    )

    assert len(matches) == 1
    assert matches[0].section.heading == "Điều 2. Thử việc"
    assert matches[0].score > 0


def test_rule_retriever_finds_salary_section():
    sections = segment_contract(
        [
            "Điều 1. Công việc",
            "Người lao động làm chuyên viên dữ liệu.",
            "Điều 3. Tiền lương",
            "Lương theo công việc là 18.000.000 đồng/tháng.",
            "Tiền lương được trả vào ngày 05 hằng tháng.",
        ]
    )

    retriever = RuleClauseRetriever()

    matches = retriever.retrieve(
        sections=sections,
        category="salary",
        top_k=1,
    )

    assert len(matches) == 1
    assert matches[0].section.heading == "Điều 3. Tiền lương"


def test_rule_retriever_ranks_heading_match_first():
    sections = segment_contract(
        [
            "Điều 1. Công việc",
            "Người lao động được trả lương theo quy định của công ty.",
            "Điều 3. Tiền lương",
            "Mức lương là 18.000.000 đồng/tháng.",
        ]
    )

    retriever = RuleClauseRetriever()

    matches = retriever.retrieve(
        sections=sections,
        category="salary",
        top_k=2,
    )

    assert len(matches) == 2
    assert matches[0].section.heading == "Điều 3. Tiền lương"


def test_rule_retriever_demonstrates_lexical_limitation():
    sections = segment_contract(
        [
            "Điều 1. Công việc",
            "Người lao động làm chuyên viên dữ liệu.",
            "Điều 2. Giai đoạn đánh giá",
            (
                "Trong hai tháng đầu kể từ ngày nhận việc, "
                "người lao động được đánh giá khả năng đáp ứng vị trí."
            ),
        ]
    )

    retriever = RuleClauseRetriever()

    matches = retriever.retrieve(
        sections=sections,
        category="probation",
        top_k=1,
    )

    assert matches == []


def test_rule_retriever_rejects_unknown_category():
    sections = segment_contract(
        [
            "Điều 1. Công việc",
            "Người lao động làm chuyên viên dữ liệu.",
        ]
    )

    retriever = RuleClauseRetriever()

    try:
        retriever.retrieve(
            sections=sections,
            category="unknown",
        )
    except ValueError as exc:
        assert "Unsupported contract review category" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
