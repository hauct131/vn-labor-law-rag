from __future__ import annotations

import re

from .models import CategoryRule
from .text_utils import (
    _ascii,
    _has_pay_date,
    _is_contract_section_heading,
    _is_probation_only_salary,
    _tokens,
)


def _excerpt_for(rule: CategoryRule, paragraphs: list[str]) -> str:
    scored: list[tuple[int, int]] = []
    for index, paragraph in enumerate(paragraphs):
        plain = _ascii(paragraph)
        paragraph_tokens = set(_tokens(paragraph))
        term_hits = sum(1 for term in rule.terms if _ascii(term) in plain)
        if term_hits == 0:
            continue
        if rule.key == "salary" and _is_probation_only_salary(paragraph):
            continue
        score = term_hits * 4
        score += sum(
            1 for token in set(_tokens(rule.title)) if token in paragraph_tokens
        )
        if rule.key == "salary":
            if re.search(r"\d[\d. ,]{3,}\s*(?:dong|vnd)\b", plain):
                score += 8
            if _has_pay_date(paragraph):
                score += 4
        if re.search(r"\d", paragraph):
            score += 2
        if len(paragraph) > 80:
            score += 1
        if score:
            scored.append((score, index))
    if not scored:
        return ""
    scored.sort(key=lambda item: (-item[0], item[1]))
    index = scored[0][1]

    # Include the complete numbered contract section so DOCX table rows and
    # sibling paragraphs remain available to the deterministic detectors.
    section_start: int | None = None
    for candidate_index in range(index, -1, -1):
        candidate = paragraphs[candidate_index]
        if not _is_contract_section_heading(candidate):
            continue
        heading_plain = _ascii(candidate)
        if any(_ascii(term) in heading_plain for term in rule.terms):
            section_start = candidate_index
        break

    if section_start is None:
        return paragraphs[index][:1600]

    parts: list[str] = []
    for candidate_index in range(section_start, len(paragraphs)):
        candidate = paragraphs[candidate_index]
        if candidate_index > section_start and _is_contract_section_heading(candidate):
            break
        parts.append(candidate)
        if len("\n".join(parts)) >= 1600:
            break
    return "\n".join(parts)[:1600]
