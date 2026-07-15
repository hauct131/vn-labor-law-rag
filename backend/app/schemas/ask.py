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
    document_number: str | None = None
    article_number: str | None = None
    clause_number: str | None = None
    point: str | None = None
    content: str
    score: float | None = None
    retrieval_origin: str | None = None
    relation_type: str | None = None


class AskResponse(BaseModel):
    answer: str
    method: RetrievalMethod
    sources: list[LegalSource]
    retrieval_ms: float
    generation_ms: float
    total_ms: float
    insufficient_evidence: bool = False
