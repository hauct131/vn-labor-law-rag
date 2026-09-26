from dataclasses import dataclass
from typing import Protocol

from .models import ContractSection


@dataclass(frozen=True)
class ClauseMatch:
    section: ContractSection
    score: float


class ClauseRetriever(Protocol):
    def retrieve(
        self,
        sections: list[ContractSection],
        category: str,
        top_k: int = 3,
    ) -> list[ClauseMatch]:
        ...