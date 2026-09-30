from dataclasses import dataclass, field
from app.schemas.ask import LegalSource


@dataclass(frozen=True)
class ContractBlock:
    index: int
    text: str
    kind: str = "paragraph"


@dataclass
class ContractSection:
    index: int
    heading: str | None
    blocks: list[ContractBlock] = field(default_factory=list)

    @property
    def text(self) -> str:
        parts = []

        if self.heading:
            parts.append(self.heading)

        parts.extend(block.text for block in self.blocks)

        return "\n".join(parts).strip()


@dataclass(frozen=True)
class FindingDraft:
    category: str
    title: str
    severity: str
    contract_excerpt: str
    analysis: str
    recommendation: str
    evidence_status: str
    sources: list[LegalSource]


@dataclass(frozen=True)
class ReviewDraft:
    summary: str
    findings: list[FindingDraft]


@dataclass(frozen=True)
class CategoryRule:
    key: str
    title: str
    query: str
    terms: tuple[str, ...]
    preferred_article_codes: tuple[str, ...]
    recommendation: str
