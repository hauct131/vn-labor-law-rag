from dataclasses import dataclass, field


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
