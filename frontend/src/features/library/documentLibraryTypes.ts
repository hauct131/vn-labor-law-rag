import type {
  LegalArticleResponse,
  LegalArticleUnit,
} from '../../api/legalArticleTypes'

export type { LegalArticleResponse, LegalArticleUnit }

export interface PaginationMeta {
  page: number
  page_size: number
  total: number
  total_pages: number
}

export interface DocumentSummary {
  document_id: string
  document_number: string
  title: string | null
  source_type: string | null
  issuing_authority: string | null
  issued_date: string | null
  effective_date: string | null
  legal_status_code: string | null
  article_count: number
  official_url: string | null
  source_adapter: string | null
}

export interface DocumentDetail extends DocumentSummary {
  law_as_of: string | null
  url_status: string | null
  url_last_checked_at: string | null
}

export interface DocumentListResponse {
  release_id: string
  law_as_of: string | null
  pagination: PaginationMeta
  documents: DocumentSummary[]
}

export interface ArticleSummary {
  article_code: string
  article_number: number | null
  title: string | null
  heading: string | null
  chapter_number: string | null
  chapter_title: string | null
  section_number: string | null
  section_title: string | null
}

export interface ArticleListResponse {
  document_id: string
  document_number: string
  pagination: PaginationMeta
  articles: ArticleSummary[]
}
