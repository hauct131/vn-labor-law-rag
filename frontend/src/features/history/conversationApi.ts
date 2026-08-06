import { API_BASE_URL } from '../../api/apiConfig'
import type {
  ConversationDetail,
  ConversationSummary,
  SavedAnswer,
} from './conversationTypes'
import { getClientId } from './clientIdentity'

async function readApiError(response: Response) {
  try {
    const body = await response.json() as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // Fall through to a stable HTTP error message.
  }
  return `Yêu cầu thất bại (HTTP ${response.status}).`
}

function headers(json = false): HeadersInit {
  return {
    'X-Client-Id': getClientId(),
    ...(json ? { 'Content-Type': 'application/json' } : {}),
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init)
  if (!response.ok) throw new Error(await readApiError(response))
  return await response.json() as T
}

export async function fetchConversations(signal?: AbortSignal) {
  const body = await request<{ conversations: ConversationSummary[] }>(
    '/conversations',
    { headers: headers(), signal },
  )
  return body.conversations
}

export function fetchConversation(conversationId: string, signal?: AbortSignal) {
  return request<ConversationDetail>(
    `/conversations/${encodeURIComponent(conversationId)}`,
    { headers: headers(), signal },
  )
}

export function renameConversation(conversationId: string, title: string) {
  return request<ConversationDetail>(
    `/conversations/${encodeURIComponent(conversationId)}`,
    {
      method: 'PATCH',
      headers: headers(true),
      body: JSON.stringify({ title }),
    },
  )
}

export async function deleteConversation(conversationId: string) {
  const response = await fetch(
    `${API_BASE_URL}/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'DELETE', headers: headers() },
  )
  if (!response.ok) throw new Error(await readApiError(response))
}

export async function saveBookmark(messageId: string, note: string | null = null) {
  return request<{ id: string; message_id: string }>(
    `/bookmarks/${encodeURIComponent(messageId)}`,
    {
      method: 'PUT',
      headers: headers(true),
      body: JSON.stringify({ note }),
    },
  )
}

export async function deleteBookmark(messageId: string) {
  const response = await fetch(
    `${API_BASE_URL}/bookmarks/${encodeURIComponent(messageId)}`,
    { method: 'DELETE', headers: headers() },
  )
  if (!response.ok) throw new Error(await readApiError(response))
}

export async function fetchSavedAnswers(signal?: AbortSignal) {
  const body = await request<{ items: SavedAnswer[] }>('/bookmarks', {
    headers: headers(),
    signal,
  })
  return body.items
}
