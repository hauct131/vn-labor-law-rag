import type { LegalSource, RetrievalMethod } from '../../api/qaTypes'

export type StoredSource = LegalSource & { id: string }

export type StoredMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  corpus_release_id: string | null
  retrieval_method: RetrievalMethod | null
  retrieval_ms: number | null
  generation_ms: number | null
  total_ms: number | null
  model: string | null
  insufficient_evidence: boolean
  out_of_scope: boolean
  generation_failed: boolean
  bookmarked: boolean
  bookmark_note: string | null
  created_at: string
  sources: StoredSource[]
}

export type ConversationSummary = {
  id: string
  title: string
  message_count: number
  last_message_preview: string | null
  created_at: string
  updated_at: string
}

export type ConversationDetail = {
  id: string
  title: string
  created_at: string
  updated_at: string
  messages: StoredMessage[]
}

export type SavedAnswer = {
  bookmark: {
    id: string
    message_id: string
    note: string | null
    created_at: string
  }
  conversation_id: string
  conversation_title: string
  question: string | null
  answer: StoredMessage
}
