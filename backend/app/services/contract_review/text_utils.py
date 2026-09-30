from __future__ import annotations

import re
import unicodedata
from typing import Any


def _ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _ascii(value))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    return [part for part in paragraphs if len(part) >= 12]


def _is_negated_probation_mention(plain: str) -> bool:
    """Return whether the contract explicitly states that probation is not applied."""
    patterns = (
        r"\bkhong\s+co\s+(?:dieu\s+khoan|thoa\s+thuan)\s+thu\s+viec\b",
        r"\bkhong\s+ap\s+dung(?:\s+(?:che\s+do|dieu\s+khoan))?\s+thu\s+viec\b",
        r"\bkhong\s+thoa\s+thuan(?:\s+ve)?\s+thu\s+viec\b",
        r"\bkhong\s+(?:phai\s+)?thu\s+viec\b",
    )
    return any(re.search(pattern, plain) for pattern in patterns)


def _is_contract_section_heading(value: str) -> bool:
    plain = _ascii(value)
    return (
        bool(re.match(r"^dieu\s+\d+[.:]", plain))
        and len(re.findall(r"\d+", plain)) == 1
        and len(value) < 120
    )


def _plain_text(value: str) -> str:
    """Normalize accents and whitespace while preserving semantic distance."""
    return re.sub(r"\s+", " ", _ascii(value)).strip()


def _has_pay_date(value: str) -> bool:
    plain = _plain_text(value)
    payment_phrase = r"(?:tra(?:\s+luong)?|thanh\s+toan|chi\s+tra)"
    day_of_month = r"(?:0?[1-9]|[12]\d|3[01])"
    return bool(
        re.search(
            rf"\b{payment_phrase}\b.{{0,100}}\bngay\s+{day_of_month}\b"
            rf"|\bngay\s+{day_of_month}\b.{{0,100}}\b{payment_phrase}\b",
            plain,
        )
    )


def _has_main_salary_evidence(value: str) -> bool:
    """Return True if text contains main/official salary evidence separate from probation compensation."""
    plain = _ascii(value)
    if "thu viec" not in plain:
        return True

    clauses = re.split(r"(?<!\d)[.;\n]+(?!\d)", value)
    for clause in clauses:
        c_plain = _ascii(clause)
        if not c_plain.strip():
            continue
        if "thu viec" in c_plain:
            match = re.search(
                r"(?:luong|muc\s+luong)\s+chinh\s+thuc[^\d]{0,30}(\d[\d. ,]{3,}\s*(?:dong|vnd|trieu)\b|\b\d+\s*trieu\b)",
                c_plain,
            )
            if match:
                return True
            continue

        has_amount = bool(
            re.search(r"\d[\d. ,]{3,}\s*(?:dong|vnd|trieu)\b|\b\d+\s*trieu\b", c_plain)
        )
        has_date = _has_pay_date(clause)
        has_salary_keyword = any(
            kw in c_plain for kw in ("luong", "tien luong", "muc luong", "thu lao")
        )

        if (has_amount or has_date) and has_salary_keyword:
            return True
        if "luong chinh thuc" in c_plain or "muc luong chinh thuc" in c_plain:
            return True

    return False


def _is_probation_only_salary(value: str) -> bool:
    """Return True if text describes probation compensation without main salary evidence."""
    plain = _ascii(value)
    if "thu viec" not in plain:
        return False
    return not _has_main_salary_evidence(value)




def _unit_numbers(patterns: tuple[str, ...], value: str) -> list[int]:
    plain = _plain_text(value)
    numbers: list[int] = []
    for pattern in patterns:
        numbers.extend(
            int(match.group("value"))
            for match in re.finditer(pattern, plain)
        )
    return numbers


def _number_near_anchors(
    value: str,
    *,
    anchors: tuple[str, ...],
    number_pattern: str,
    max_distance: int = 90,
) -> int | None:
    """Return the unit-bearing number nearest to a relevant legal phrase."""
    plain = _plain_text(value)
    anchor_spans = [
        match.span()
        for anchor in anchors
        for match in re.finditer(re.escape(anchor), plain)
    ]
    if not anchor_spans:
        return None

    candidates: list[tuple[int, int, int]] = []
    for match in re.finditer(number_pattern, plain):
        number_center = (match.start() + match.end()) // 2
        distance = min(
            abs(number_center - ((start + end) // 2))
            for start, end in anchor_spans
        )
        if distance <= max_distance:
            candidates.append((distance, match.start(), int(match.group("value"))))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]
