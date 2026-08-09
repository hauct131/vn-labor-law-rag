import { readApiError, sessionFetch } from '../auth/authApi'
import type { ContractReview, ContractReviewListItem } from './contractReviewTypes'

export async function createContractReview(file: File) {
  const body = new FormData()
  body.set('file', file)
  body.set('method', 'sparse')
  const response = await sessionFetch('/contract-reviews', {
    method: 'POST',
    body,
  }, true)
  if (!response.ok) throw new Error(await readApiError(response))
  return await response.json() as ContractReview
}

export async function fetchContractReviews(signal?: AbortSignal) {
  const response = await sessionFetch('/contract-reviews?limit=50&offset=0', { signal })
  if (!response.ok) throw new Error(await readApiError(response))
  const body = await response.json() as { items: ContractReviewListItem[] }
  return body.items
}

export async function fetchContractReview(reviewId: string, signal?: AbortSignal) {
  const response = await sessionFetch(`/contract-reviews/${encodeURIComponent(reviewId)}`, { signal })
  if (!response.ok) throw new Error(await readApiError(response))
  return await response.json() as ContractReview
}

export async function deleteContractReview(reviewId: string) {
  const response = await sessionFetch(
    `/contract-reviews/${encodeURIComponent(reviewId)}`,
    { method: 'DELETE' },
    true,
  )
  if (!response.ok) throw new Error(await readApiError(response))
}
