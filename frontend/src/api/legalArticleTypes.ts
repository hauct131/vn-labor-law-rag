export interface LegalArticleUnit {
  chunk_id: string
  label: string
  unit_type: string
  clause_number: string | null
  point_labels: string[]
  table_index: number | null
  segment_index: number | null
  content: string
}

export interface LegalArticleResponse {
  article_code: string
  article_number: string | null
  article_title: string | null
  citation_label: string
  document_title: string | null
  document_number: string | null
  source_type: string | null
  source_document_id: string | null
  source_note_text: string | null
  topic_code: string | null
  topic_name: string | null
  chapter_number: string | null
  chapter_title: string | null
  section_number: string | null
  section_title: string | null
  official_url: string | null
  original_source_urls: string[]
  url_status: string | null
  url_last_checked_at: string | null
  chunk_count: number
  units: LegalArticleUnit[]
}
