import type { LegalSource, RetrievalMethod } from '../../api/qaTypes'

export type ContractFinding = {
  id: string
  category: string
  title: string
  severity: 'info' | 'attention' | 'warning' | 'insufficient_evidence'
  contract_excerpt: string
  analysis: string
  recommendation: string
  evidence_status: 'supported' | 'insufficient_evidence'
  sources: LegalSource[]
}

export type ContractReview = {
  id: string
  original_filename: string
  file_sha256: string
  mime_type: string
  file_size_bytes: number
  method: RetrievalMethod
  status: string
  summary: string
  extracted_character_count: number
  findings: ContractFinding[]
  created_at: string
  updated_at: string
}

export type ContractReviewListItem = {
  id: string
  original_filename: string
  status: string
  summary: string
  finding_count: number
  attention_count: number
  warning_count: number
  missing_count: number
  created_at: string
}
