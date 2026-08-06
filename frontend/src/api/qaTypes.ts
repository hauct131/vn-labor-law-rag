export type RetrievalMethod = 'sparse' | 'dense' | 'hybrid'

export type LegalSource = {
  source_id: string | null
  chunk_id: string
  article_code: string | null
  article_number: string | null
  article_title: string | null
  document_title: string | null
  document_number: string | null
  citation_label: string | null
  clause_number: string | null
  point_labels: string[]
  content: string
  score: number | null
  rank: number
  retrieval_origin: string | null
  source_type: string | null
  source_url: string | null
  component_ranks: Record<string, number>
}

export type AskResponse = {
  answer: string
  method: RetrievalMethod
  sources: LegalSource[]
  retrieval_ms: number
  generation_ms: number
  total_ms: number
  model: string | null
  insufficient_evidence: boolean
  out_of_scope: boolean
  generation_failed: boolean
  history_saved: boolean
  history_error: string | null
  conversation_id: string | null
  user_message_id: string | null
  assistant_message_id: string | null
}
