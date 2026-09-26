import pytest

from app.services.contract_review.e5_retriever import (
    _cosine,
)


def test_cosine_identical_vectors():
    score = _cosine(
        (1.0, 2.0, 3.0),
        (1.0, 2.0, 3.0),
    )

    assert score == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    score = _cosine(
        (1.0, 0.0),
        (0.0, 1.0),
    )

    assert score == pytest.approx(0.0)


def test_cosine_opposite_vectors():
    score = _cosine(
        (1.0, 0.0),
        (-1.0, 0.0),
    )

    assert score == pytest.approx(-1.0)


def test_cosine_rejects_dimension_mismatch():
    with pytest.raises(
        ValueError,
        match="dimensions do not match",
    ):
        _cosine(
            (1.0, 2.0),
            (1.0,),
        )


def test_cosine_zero_vector_is_zero():
    score = _cosine(
        (0.0, 0.0),
        (1.0, 2.0),
    )

    assert score == 0.0
