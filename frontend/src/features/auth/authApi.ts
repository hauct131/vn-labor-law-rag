import { API_BASE_URL } from '../../api/apiConfig'
import { getClientId } from '../history/clientIdentity'
import type { AuthResult, AuthUser } from './authTypes'

const CSRF_COOKIE_NAME = 'legal_rag_csrf'
export const AUTH_SESSION_EXPIRED_EVENT = 'legal-rag-auth-session-expired'
let activeCsrfToken: string | null = null

async function readApiError(response: Response) {
  try {
    const body = await response.json() as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // Fall through to the stable HTTP error below.
  }
  return `Yêu cầu thất bại (HTTP ${response.status}).`
}

function readCookie(name: string) {
  const prefix = `${encodeURIComponent(name)}=`
  for (const part of document.cookie.split(';')) {
    const value = part.trim()
    if (value.startsWith(prefix)) {
      return decodeURIComponent(value.slice(prefix.length))
    }
  }
  return null
}

function rememberCsrf(token: string | null) {
  activeCsrfToken = token
}

export async function sessionFetch(
  path: string,
  init: RequestInit = {},
  requireCsrf = false,
) {
  const headers = new Headers(init.headers)
  if (requireCsrf) {
    const csrfToken = activeCsrfToken || readCookie(CSRF_COOKIE_NAME)
    if (!csrfToken) throw new Error('Phiên đăng nhập thiếu CSRF token. Hãy đăng nhập lại.')
    headers.set('X-CSRF-Token', csrfToken)
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  })
  const isAuthenticationProbe = path === '/auth/me'
    || path === '/auth/login'
    || path === '/auth/register'
  if (response.status === 401 && !isAuthenticationProbe) {
    rememberCsrf(null)
    window.dispatchEvent(new Event(AUTH_SESSION_EXPIRED_EVENT))
  }
  return response
}

async function authRequest(
  path: string,
  body: Record<string, string>,
): Promise<AuthResult> {
  const response = await sessionFetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Client-Id': getClientId(),
    },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error(await readApiError(response))
  const result = await response.json() as AuthResult
  rememberCsrf(result.csrf_token)
  return result
}

export function registerAccount(payload: {
  email: string
  password: string
  display_name: string
}) {
  return authRequest('/auth/register', payload)
}

export function loginAccount(payload: { email: string; password: string }) {
  return authRequest('/auth/login', payload)
}

export async function fetchCurrentUser(signal?: AbortSignal): Promise<AuthUser | null> {
  const response = await sessionFetch('/auth/me', { signal })
  if (response.status === 401) {
    rememberCsrf(null)
    return null
  }
  if (!response.ok) throw new Error(await readApiError(response))
  const result = await response.json() as AuthResult
  rememberCsrf(result.csrf_token)
  return result.user
}

export async function logoutAccount() {
  const response = await sessionFetch('/auth/logout', { method: 'POST' }, true)
  if (response.status !== 401 && !response.ok) {
    throw new Error(await readApiError(response))
  }
  rememberCsrf(null)
}

export { readApiError }
