const CLIENT_ID_KEY = 'legal_rag_client_id_v1'
const ACTIVE_CONVERSATION_KEY = 'legal_rag_active_conversation_v1'
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

let volatileClientId: string | null = null
let volatileConversationId: string | null = null

function fallbackUuid() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (token) => {
    const random = Math.floor(Math.random() * 16)
    const value = token === 'x' ? random : (random & 0x3) | 0x8
    return value.toString(16)
  })
}

function newUuid() {
  return typeof globalThis.crypto?.randomUUID === 'function'
    ? globalThis.crypto.randomUUID()
    : fallbackUuid()
}

function readStorage(key: string) {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    // Privacy modes may disable localStorage. The in-memory fallback keeps the
    // current page usable, although identity will not survive a full reload.
  }
}

export function getClientId() {
  const existing = readStorage(CLIENT_ID_KEY) || volatileClientId
  if (existing && UUID_PATTERN.test(existing)) return existing

  const value = newUuid()
  volatileClientId = value
  writeStorage(CLIENT_ID_KEY, value)
  return value
}

export function getActiveConversationId() {
  const value = readStorage(ACTIVE_CONVERSATION_KEY) || volatileConversationId
  if (!value || UUID_PATTERN.test(value)) return value
  setActiveConversationId(null)
  return null
}

export function setActiveConversationId(value: string | null) {
  volatileConversationId = value
  writeStorage(ACTIVE_CONVERSATION_KEY, value)
}
