from pydantic import BaseModel, Field


class LegalArticleUnit(BaseModel):
    chunk_id: str
    label: str
    unit_type: str
    clause_number: str | None = None
    point_labels: list[str] = Field(default_factory=list)
    table_index: int | None = None
    segment_index: int | None = None
    content: str


class LegalArticleResponse(BaseModel):
    article_code: str
    article_number: str | None = None
    article_title: str | None = None
    citation_label: str
    document_title: str | None = None
    document_number: str | None = None
    source_type: str | None = None
    source_document_id: str | None = None
    source_note_text: str | None = None
    topic_code: str | None = None
    topic_name: str | None = None
    chapter_number: str | None = None
    chapter_title: str | None = None
    section_number: str | None = None
    section_title: str | None = None
    official_url: str | None = None
    original_source_urls: list[str] = Field(default_factory=list)
    url_status: str | None = None
    url_last_checked_at: str | None = None
    chunk_count: int
    units: list[LegalArticleUnit]
