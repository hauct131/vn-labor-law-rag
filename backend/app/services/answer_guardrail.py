"""Deterministic validation for evidence-grounded LLM output."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


_CITATION_RE = re.compile(r"\[S([1-9]\d*)\]", re.IGNORECASE)
_SOURCE_ID_RE = re.compile(r"^S[1-9]\d*$")


class AnswerStatus(StrEnum):
    ANSWERABLE = "answerable"
    OUT_OF_SCOPE = "out_of_scope"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class GuardedLegalAnswer(BaseModel):
    """Structured answer proposed by the language model."""

    model_config = ConfigDict(extra="forbid")

    status: AnswerStatus
    answer: str = Field(min_length=1, max_length=12000)
    cited_source_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("answer")
    @classmethod
    def normalize_answer(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("answer must not be blank")
        return normalized

    @field_validator("cited_source_ids")
    @classmethod
    def normalize_source_ids(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip().upper() for value in values]
        if any(not _SOURCE_ID_RE.fullmatch(value) for value in normalized):
            raise ValueError("cited_source_ids contains an invalid source id")
        if len(set(normalized)) != len(normalized):
            raise ValueError("cited_source_ids must not contain duplicates")
        return normalized


class GuardrailValidationError(ValueError):
    """Raised when model output cannot safely be exposed."""


def extract_inline_citations(answer: str) -> set[str]:
    return {f"S{match}" for match in _CITATION_RE.findall(answer)}


def _extract_json_object(raw_output: str) -> str:
    text = raw_output.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().casefold() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise GuardrailValidationError("model output does not contain JSON")
    return text[start : end + 1]


def parse_guarded_answer(raw_output: str) -> GuardedLegalAnswer:
    """Parse and validate the structured JSON returned by the model."""

    try:
        payload = json.loads(_extract_json_object(raw_output))
    except (json.JSONDecodeError, TypeError) as exc:
        raise GuardrailValidationError("model output is not valid JSON") from exc

    try:
        return GuardedLegalAnswer.model_validate(payload)
    except ValidationError as exc:
        raise GuardrailValidationError("model output violates answer schema") from exc


def validate_guarded_answer(
    output: GuardedLegalAnswer,
    available_source_ids: Iterable[str],
) -> GuardedLegalAnswer:
    """Enforce citation and status invariants using backend code."""

    available = {str(value).strip().upper() for value in available_source_ids}
    declared = set(output.cited_source_ids)
    inline = extract_inline_citations(output.answer)

    if output.status == AnswerStatus.ANSWERABLE:
        if not declared:
            raise GuardrailValidationError(
                "answerable output must declare at least one citation"
            )
        if not declared.issubset(available):
            raise GuardrailValidationError(
                "answerable output cites a source outside the provided context"
            )
        if inline != declared:
            raise GuardrailValidationError(
                "inline citations and cited_source_ids do not match"
            )
        return output

    if declared or inline:
        raise GuardrailValidationError(
            "non-answerable output must not contain citations"
        )
    return output


def parse_and_validate_guarded_answer(
    raw_output: str,
    available_source_ids: Iterable[str],
) -> GuardedLegalAnswer:
    return validate_guarded_answer(
        parse_guarded_answer(raw_output),
        available_source_ids,
    )
