from enum import StrEnum

from pydantic import BaseModel, Field


class RetrievalMethod(StrEnum):
    SPARSE = "sparse"
    HYBRID = "hybrid"
    GRAPH_ENHANCED = "graph_enhanced"


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    method: RetrievalMethod


class LegalSource(BaseModel):
    chunk_id: str
    article_code: str | None = None
    article_number: str | None = None
    article_title: str | None = None
    document_title: str | None = None
    document_number: str | None = None
    citation_label: str
    clause_number: str | None = None
    point_labels: list[str] = Field(default_factory=list)
    content: str
    score: float | None = None
    rank: int
    retrieval_origin: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    component_ranks: dict[str, int] = Field(default_factory=dict)


class AskResponse(BaseModel):
    answer: str
    method: RetrievalMethod
    sources: list[LegalSource]
    retrieval_ms: float
    generation_ms: float
    total_ms: float
    model: str | None = None
    insufficient_evidence: bool = False
