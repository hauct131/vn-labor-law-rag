from app.services.contract_review.hybrid_retriever import (
    HybridClauseRetriever,
)
from app.services.contract_review.retriever import (
    ClauseMatch,
)
from app.services.contract_review.segmenter import (
    segment_contract,
)


class StubRetriever:
    def __init__(self, order):
        self.order = order

    def retrieve(
        self,
        sections,
        category,
        top_k=3,
    ):
        by_index = {
            section.index: section
            for section in sections
        }

        return [
            ClauseMatch(
                section=by_index[index],
                score=float(len(self.order) - rank),
            )
            for rank, index in enumerate(
                self.order[:top_k]
            )
        ]


def _sections():
    return segment_contract(
        [
            "Điều 1. Công việc",
            "Người lao động thực hiện công việc.",
            "Điều 2. Tiền lương",
            "Mức lương là 18.000.000 đồng/tháng.",
            "Điều 3. Chấm dứt",
            "Hai bên thực hiện chấm dứt theo quy định.",
        ]
    )


def test_rrf_rewards_agreement():
    sections = _sections()

    retriever = HybridClauseRetriever(
        bm25=StubRetriever([1, 0, 2]),
        e5=StubRetriever([1, 2, 0]),
        rrf_k=60,
    )

    matches = retriever.retrieve(
        sections,
        "salary",
        top_k=3,
    )

    assert matches[0].section.index == 1


def test_rrf_returns_requested_top_k():
    sections = _sections()

    retriever = HybridClauseRetriever(
        bm25=StubRetriever([0, 1, 2]),
        e5=StubRetriever([2, 1, 0]),
    )

    matches = retriever.retrieve(
        sections,
        "salary",
        top_k=2,
    )

    assert len(matches) == 2


def test_rrf_tie_breaks_by_section_index():
    sections = _sections()

    retriever = HybridClauseRetriever(
        bm25=StubRetriever([0, 1, 2]),
        e5=StubRetriever([2, 1, 0]),
        rrf_k=60,
    )

    matches = retriever.retrieve(
        sections,
        "salary",
        top_k=3,
    )

    # section 0 and 2 receive symmetric RRF scores.
    # The deterministic secondary key must therefore rank
    # the smaller section index first.
    assert matches[0].score == matches[1].score
    assert matches[0].section.index == 0
    assert matches[1].section.index == 2

    # Section 1 is rank 2 in both component retrievers,
    # which gives it a slightly smaller RRF score.
    assert matches[2].section.index == 1
    assert matches[1].score > matches[2].score


def test_rrf_rejects_invalid_k():
    sections = _sections()

    try:
        HybridClauseRetriever(
            bm25=StubRetriever([0]),
            e5=StubRetriever([0]),
            rrf_k=0,
        )
    except ValueError as exc:
        assert "rrf_k" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
