import type {
  ConversationDetail,
  ConversationSummary,
  SavedAnswer,
} from './conversationTypes'
import { readApiError, sessionFetch } from '../auth/authApi'

async function request<T>(
  path: string,
  init?: RequestInit,
  requireCsrf = false,
): Promise<T> {
  const response = await sessionFetch(path, init, requireCsrf)
  if (!response.ok) throw new Error(await readApiError(response))
  return await response.json() as T
}

export async function fetchConversations(signal?: AbortSignal) {
  const body = await request<{ conversations: ConversationSummary[] }>(
    '/conversations',
    { signal },
  )
  return body.conversations
}

export function fetchConversation(conversationId: string, signal?: AbortSignal) {
  return request<ConversationDetail>(
    `/conversations/${encodeURIComponent(conversationId)}`,
    { signal },
  )
}

export function renameConversation(conversationId: string, title: string) {
  return request<ConversationDetail>(
    `/conversations/${encodeURIComponent(conversationId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    },
    true,
  )
}

export async function deleteConversation(conversationId: string) {
  const response = await sessionFetch(
    `/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'DELETE' },
    true,
  )
  if (!response.ok) throw new Error(await readApiError(response))
}

export async function saveBookmark(messageId: string, note: string | null = null) {
  return request<{ id: string; message_id: string }>(
    `/bookmarks/${encodeURIComponent(messageId)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    },
    true,
  )
}

export async function deleteBookmark(messageId: string) {
  const response = await sessionFetch(
    `/bookmarks/${encodeURIComponent(messageId)}`,
    { method: 'DELETE' },
    true,
  )
  if (!response.ok) throw new Error(await readApiError(response))
}

export async function fetchSavedAnswers(signal?: AbortSignal) {
  const body = await request<{ items: SavedAnswer[] }>('/bookmarks', { signal })
  return body.items
}
