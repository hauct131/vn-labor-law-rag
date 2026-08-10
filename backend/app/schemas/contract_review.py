"""API schemas for labor contract review."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.ask import LegalSource, RetrievalMethod


Severity = Literal["info", "attention", "warning", "insufficient_evidence"]
EvidenceStatus = Literal["supported", "insufficient_evidence"]


class ContractReviewFindingResponse(BaseModel):
    id: str
    category: str
    title: str
    severity: Severity
    contract_excerpt: str
    analysis: str
    recommendation: str
    evidence_status: EvidenceStatus
    sources: list[LegalSource]

    model_config = ConfigDict(from_attributes=True)


class ContractReviewResponse(BaseModel):
    id: str
    original_filename: str
    file_sha256: str
    mime_type: str
    file_size_bytes: int
    method: RetrievalMethod
    status: str
    summary: str
    extracted_character_count: int
    findings: list[ContractReviewFindingResponse]
    created_at: datetime
    updated_at: datetime


class ContractReviewListItem(BaseModel):
    id: str
    original_filename: str
    status: str
    summary: str
    finding_count: int
    attention_count: int
    warning_count: int
    created_at: datetime


class ContractReviewListResponse(BaseModel):
    items: list[ContractReviewListItem]
    total: int
    limit: int
    offset: int
