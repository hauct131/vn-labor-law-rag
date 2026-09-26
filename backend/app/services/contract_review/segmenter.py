import re
import unicodedata

from .models import ContractBlock, ContractSection


def _ascii(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFD",
        value.casefold().replace("đ", "d"),
    )

    return "".join(
        char
        for char in normalized
        if unicodedata.category(char) != "Mn"
    )


def is_contract_heading(value: str) -> bool:
    plain = _ascii(value)

    return (
        bool(re.match(r"^dieu\s+\d+[.:]", plain))
        and len(re.findall(r"\d+", plain)) == 1
        and len(value) < 120
    )


def segment_contract(
    paragraphs: list[str],
) -> list[ContractSection]:
    sections: list[ContractSection] = []
    current: ContractSection | None = None

    for block_index, raw_text in enumerate(paragraphs):
        text = raw_text.strip()

        if not text:
            continue

        if is_contract_heading(text):
            current = ContractSection(
                index=len(sections),
                heading=text,
            )
            sections.append(current)
            continue

        if current is None:
            current = ContractSection(
                index=len(sections),
                heading=None,
            )
            sections.append(current)

        current.blocks.append(
            ContractBlock(
                index=block_index,
                text=text,
            )
        )

    return sections
