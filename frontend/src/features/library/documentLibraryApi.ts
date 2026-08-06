import { API_BASE_URL } from '../../api/apiConfig'
import type {
  ArticleListResponse,
  DocumentDetail,
  DocumentListResponse,
  LegalArticleResponse,
} from './documentLibraryTypes'

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // Fallback if parsing JSON fails
  }
  return `Yêu cầu thất bại (HTTP ${response.status}).`
}

export async function fetchDocuments(
  params?: {
    q?: string
    document_type?: string
    page?: number
    page_size?: number
  },
  signal?: AbortSignal,
): Promise<DocumentListResponse> {
  const searchParams = new URLSearchParams()
  if (params?.q && params.q.trim()) {
    searchParams.set('q', params.q.trim())
  }
  if (params?.document_type && params.document_type.trim()) {
    searchParams.set('document_type', params.document_type.trim())
  }
  if (params?.page !== undefined) {
    searchParams.set('page', String(params.page))
  }
  if (params?.page_size !== undefined) {
    searchParams.set('page_size', String(params.page_size))
  }

  const queryString = searchParams.toString()
  const url = `${API_BASE_URL}/documents${queryString ? `?${queryString}` : ''}`

  const response = await fetch(url, { signal })
  if (!response.ok) {
    throw new Error(await readError(response))
  }
  return (await response.json()) as DocumentListResponse
}

export async function fetchDocument(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentDetail> {
  const encodedId = encodeURIComponent(documentId)
  const url = `${API_BASE_URL}/documents/${encodedId}`

  const response = await fetch(url, { signal })
  if (!response.ok) {
    throw new Error(await readError(response))
  }
  return (await response.json()) as DocumentDetail
}

export async function fetchDocumentArticles(
  documentId: string,
  params?: {
    q?: string
    page?: number
    page_size?: number
  },
  signal?: AbortSignal,
): Promise<ArticleListResponse> {
  const encodedId = encodeURIComponent(documentId)
  const searchParams = new URLSearchParams()
  if (params?.q && params.q.trim()) {
    searchParams.set('q', params.q.trim())
  }
  if (params?.page !== undefined) {
    searchParams.set('page', String(params.page))
  }
  if (params?.page_size !== undefined) {
    searchParams.set('page_size', String(params.page_size))
  }

  const queryString = searchParams.toString()
  const url = `${API_BASE_URL}/documents/${encodedId}/articles${queryString ? `?${queryString}` : ''}`

  const response = await fetch(url, { signal })
  if (!response.ok) {
    throw new Error(await readError(response))
  }
  return (await response.json()) as ArticleListResponse
}

export async function fetchArticleSource(
  articleCode: string,
  signal?: AbortSignal,
): Promise<LegalArticleResponse> {
  const encodedCode = encodeURIComponent(articleCode)
  const url = `${API_BASE_URL}/sources/${encodedCode}`

  const response = await fetch(url, { signal })
  if (!response.ok) {
    throw new Error(await readError(response))
  }
  return (await response.json()) as LegalArticleResponse
}
