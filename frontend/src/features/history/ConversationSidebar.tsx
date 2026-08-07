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
  onRename: (conversation: ConversationSummary) => void
  onDelete: (conversation: ConversationSummary) => void
}) {
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
        {conversations.map((conversation) => (
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
                aria-label={`Đổi tên ${conversation.title}`}
                disabled={isBusy}
                onClick={() => onRename(conversation)}
              >
                ✎
              </button>
              <button
                type="button"
                aria-label={`Xóa ${conversation.title}`}
                disabled={isBusy}
                onClick={() => onDelete(conversation)}
              >
                ×
              </button>
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}
