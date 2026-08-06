import { useEffect, useRef, useState } from 'react'
import type { ConversationSummary } from './conversationTypes'

function formatUpdatedAt(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('vi-VN', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function ConversationSidebar({
  conversations,
  activeConversationId,
  isLoading,
  isBusy,
  error,
  isAuthenticated,
  onLogin,
  onNew,
  onSelect,
  onRename,
  onDelete,
}: {
  conversations: ConversationSummary[]
  activeConversationId: string | null
  isLoading: boolean
  isBusy: boolean
  error: string
  isAuthenticated: boolean
  onLogin: () => void
  onNew: () => void
  onSelect: (conversationId: string) => void
  onRename: (conversationId: string, newTitle: string) => Promise<void>
  onDelete: (conversation: ConversationSummary) => void
}) {
  const [editingConversationId, setEditingConversationId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const [isRenaming, setIsRenaming] = useState(false)
  const [renameError, setRenameError] = useState<string | null>(null)

  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingConversationId && inputRef.current) {
      inputRef.current.focus()
      inputRef.current.select()
    }
  }, [editingConversationId])

  function handleStartRename(conversation: ConversationSummary) {
    setEditingConversationId(conversation.id)
    setEditingTitle(conversation.title)
    setRenameError(null)
  }

  function handleCancelRename() {
    if (isRenaming) return
    setEditingConversationId(null)
    setEditingTitle('')
    setRenameError(null)
  }

  async function handleConfirmRename(conversation: ConversationSummary) {
    const trimmed = editingTitle.trim()
    if (!trimmed || isRenaming) return

    if (trimmed === conversation.title) {
      setEditingConversationId(null)
      setEditingTitle('')
      setRenameError(null)
      return
    }

    setIsRenaming(true)
    setRenameError(null)
    try {
      await onRename(conversation.id, trimmed)
      setEditingConversationId(null)
      setEditingTitle('')
      setRenameError(null)
    } catch (err) {
      setRenameError(err instanceof Error ? err.message : 'Không thể đổi tên hội thoại.')
    } finally {
      setIsRenaming(false)
    }
  }

  return (
    <aside className="conversation-sidebar" aria-label="Lịch sử hội thoại">
      <div className="conversation-sidebar-header">
        <div>
          <p className="eyebrow">Lịch sử</p>
          <h2>Hội thoại</h2>
        </div>
        <button
          type="button"
          className="new-conversation-button"
          disabled={isBusy || !isAuthenticated}
          onClick={onNew}
        >
          + Mới
        </button>
      </div>

      {!isAuthenticated ? (
        <div className="history-login-state">
          <p>Đăng nhập để lưu và mở lại lịch sử hội thoại.</p>
          <button type="button" onClick={onLogin}>Đăng nhập</button>
        </div>
      ) : (
        <>
          {isLoading && <p className="history-state">Đang tải lịch sử…</p>}
          {error && <p className="history-state history-error" role="alert">{error}</p>}
          {!isLoading && !error && conversations.length === 0 && (
            <p className="history-state">Chưa có hội thoại nào.</p>
          )}
        </>
      )}

      <div className="conversation-list">
        {conversations.map((conversation) => {
          const isEditing = editingConversationId === conversation.id

          if (isEditing) {
            return (
              <div
                className={`conversation-item inline-editing ${
                  activeConversationId === conversation.id ? 'active' : ''
                }`}
                key={conversation.id}
                onClick={(e) => e.stopPropagation()}
              >
                <div className="conversation-inline-rename-form">
                  <input
                    ref={inputRef}
                    type="text"
                    className="inline-rename-input"
                    aria-label="Tên hội thoại"
                    value={editingTitle}
                    disabled={isRenaming}
                    maxLength={200}
                    onChange={(e) => {
                      setEditingTitle(e.target.value)
                      if (renameError) setRenameError(null)
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        e.stopPropagation()
                        void handleConfirmRename(conversation)
                      } else if (e.key === 'Escape') {
                        e.preventDefault()
                        e.stopPropagation()
                        handleCancelRename()
                      }
                    }}
                  />
                  <div className="inline-rename-actions">
                    <button
                      type="button"
                      className="inline-rename-save-btn"
                      aria-label="Lưu tên hội thoại"
                      disabled={isRenaming || !editingTitle.trim()}
                      onClick={(e) => {
                        e.stopPropagation()
                        void handleConfirmRename(conversation)
                      }}
                    >
                      {isRenaming ? '…' : '✓'}
                    </button>
                    <button
                      type="button"
                      className="inline-rename-cancel-btn"
                      aria-label="Hủy đổi tên"
                      disabled={isRenaming}
                      onClick={(e) => {
                        e.stopPropagation()
                        handleCancelRename()
                      }}
                    >
                      ×
                    </button>
                  </div>
                </div>
                {renameError && (
                  <p className="inline-rename-error" role="alert">
                    {renameError}
                  </p>
                )}
              </div>
            )
          }

          return (
            <div
              className={`conversation-item ${
                activeConversationId === conversation.id ? 'active' : ''
              }`}
              key={conversation.id}
            >
              <button
                type="button"
                className="conversation-select"
                disabled={isBusy}
                onClick={() => onSelect(conversation.id)}
              >
                <strong>{conversation.title}</strong>
                <span>{conversation.last_message_preview || 'Hội thoại chưa có tin nhắn'}</span>
                <small>
                  {conversation.message_count} tin · {formatUpdatedAt(conversation.updated_at)}
                </small>
              </button>
              <div className="conversation-actions">
                <button
                  type="button"
                  aria-label="Đổi tên hội thoại"
                  disabled={isBusy}
                  onClick={(e) => {
                    e.stopPropagation()
                    handleStartRename(conversation)
                  }}
                >
                  ✎
                </button>
                <button
                  type="button"
                  aria-label={`Xóa ${conversation.title}`}
                  disabled={isBusy}
                  onClick={(e) => {
                    e.stopPropagation()
                    onDelete(conversation)
                  }}
                >
                  ×
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </aside>
  )
}
