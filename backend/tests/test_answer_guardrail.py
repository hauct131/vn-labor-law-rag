"""Tests for structured output and deterministic citation enforcement."""

import pytest

from backend.app.services.answer_guardrail import (
    AnswerStatus,
    GuardrailValidationError,
    parse_and_validate_guarded_answer,
)


def test_accepts_answerable_json_with_matching_citations() -> None:
    output = parse_and_validate_guarded_answer(
        """{
          "status": "answerable",
          "answer": "Người lao động được nghỉ theo quy định [S1].",
          "cited_source_ids": ["S1"]
        }""",
        {"S1", "S2"},
    )

    assert output.status == AnswerStatus.ANSWERABLE
    assert output.cited_source_ids == ["S1"]


def test_accepts_json_inside_markdown_fence() -> None:
    output = parse_and_validate_guarded_answer(
        """```json
        {
          "status": "out_of_scope",
          "answer": "Ngoài phạm vi.",
          "cited_source_ids": []
        }
        ```""",
        {"S1"},
    )

    assert output.status == AnswerStatus.OUT_OF_SCOPE


@pytest.mark.parametrize(
    "raw_output",
    [
        "không phải JSON",
        """{
          "status": "answerable",
          "answer": "Không có citation.",
          "cited_source_ids": []
        }""",
        """{
          "status": "answerable",
          "answer": "Trích nguồn giả [S9].",
          "cited_source_ids": ["S9"]
        }""",
        """{
          "status": "answerable",
          "answer": "Dùng S1 [S1].",
          "cited_source_ids": ["S2"]
        }""",
        """{
          "status": "out_of_scope",
          "answer": "Ngoài phạm vi nhưng vẫn trích [S1].",
          "cited_source_ids": ["S1"]
        }""",
        """{
          "status": "answerable",
          "answer": "Có nguồn [S1].",
          "cited_source_ids": ["S1"],
          "unexpected": true
        }""",
    ],
)
def test_rejects_unsafe_model_output(raw_output: str) -> None:
    with pytest.raises(GuardrailValidationError):
        parse_and_validate_guarded_answer(raw_output, {"S1", "S2"})
