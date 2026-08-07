import { type FormEvent, type ReactNode, useEffect, useRef, useState } from 'react'
import './App.css'
import { API_BASE_URL } from './api/apiConfig'
import type { LegalArticleResponse } from './api/legalArticleTypes'
import type { AskResponse, RetrievalMethod } from './api/qaTypes'
import './features/library/documentLibrary.css'
import { DocumentLibraryPage } from './features/library/DocumentLibraryPage'
import { DocumentDetailPage } from './features/library/DocumentDetailPage'
import { AnswerCard, type DisplayAnswer } from './features/history/AnswerCard'
import { ConversationSidebar } from './features/history/ConversationSidebar'
import { SavedAnswersPage } from './features/history/SavedAnswersPage'
import {
  deleteBookmark,
  deleteConversation,
  fetchConversation,
  fetchConversations,
  renameConversation,
  saveBookmark,
} from './features/history/conversationApi'
import {
  getActiveConversationId,
  setActiveConversationId,
} from './features/history/clientIdentity'
import type {
  ConversationDetail,
  ConversationSummary,
  StoredMessage,
} from './features/history/conversationTypes'
import './features/history/history.css'
import './features/auth/auth.css'
import './ui-refresh.css'
import { AuthDialog } from './features/auth/AuthDialog'
import {
  AUTH_SESSION_EXPIRED_EVENT,
  fetchCurrentUser,
  logoutAccount,
  sessionFetch,
} from './features/auth/authApi'
import type { AuthResult, AuthUser } from './features/auth/authTypes'

const methods: Array<{
  value: RetrievalMethod
  name: string
  description: string
}> = [
  {
    value: 'sparse',
    name: 'Sparse',
    description: 'BM25 + VnCoreNLP',
  },
  {
    value: 'dense',
    name: 'Dense',
    description: 'E5 · truy hồi theo ngữ nghĩa',
  },
  {
    value: 'hybrid',
    name: 'Hybrid',
    description: 'BM25 + Dense E5 + RRF · cấu hình đã khóa',
  },
]

const examples = [
  'Người lao động được nghỉ hằng năm bao nhiêu ngày?',
  'Những trường hợp nào đình công bị xem là bất hợp pháp?',
  'Tuổi nghỉ hưu của người lao động được quy định như thế nào?',
]

async function readError(response: Response) {
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // The fallback below is clearer than exposing an invalid provider body.
  }
  return `Yêu cầu thất bại (HTTP ${response.status}).`
}

function navigateTo(path: string) {
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

type NavAuthProps = {
  authUser: AuthUser | null
  isAuthLoading: boolean
  onOpenAuth: () => void
  onLogout: () => void
}

function TopNav({
  activeTab,
  showBackLink = false,
  authUser,
  isAuthLoading,
  onOpenAuth,
  onLogout,
}: {
  activeTab: 'qa' | 'library' | 'source' | 'saved'
  badgeText?: string
  showBackLink?: boolean
} & NavAuthProps) {
  return (
    <header className="topbar">
      <div className="topbar-left">
        <a
          className="brand"
          href="/"
          aria-label="Trang chủ"
          onClick={(e) => {
            e.preventDefault()
            navigateTo('/')
          }}
        >
          <span className="brand-mark" aria-hidden="true">§</span>
          <span className="brand-text">
            <strong>Luật Lao động Việt Nam</strong>
            <small>RAG Legal Assistant</small>
          </span>
        </a>

        <nav className="top-navigation" aria-label="Điều hướng chính">
          <button
            type="button"
            className={`nav-tab ${activeTab === 'qa' ? 'active' : ''}`}
            onClick={() => navigateTo('/')}
          >
            Hỏi đáp
          </button>
          <button
            type="button"
            className={`nav-tab ${activeTab === 'library' ? 'active' : ''}`}
            onClick={() => navigateTo('/library')}
          >
            Thư viện
          </button>
          <button
            type="button"
            className={`nav-tab ${activeTab === 'saved' ? 'active' : ''}`}
            onClick={() => navigateTo('/saved')}
          >
            Đã lưu
          </button>
        </nav>
      </div>

      <div className="topbar-right">
        {showBackLink && (
          <a
            className="back-link"
            href="/"
            onClick={(e) => {
              e.preventDefault()
              navigateTo('/')
            }}
          >
            ← Quay lại hỏi đáp
          </a>
        )}
        <div className="auth-actions">
          {authUser ? (
            <>
              <span className="auth-user">
                <strong>{authUser.display_name}</strong>
                <small>{authUser.email}</small>
              </span>
              <button className="auth-button secondary" type="button" onClick={onLogout}>
                Đăng xuất
              </button>
            </>
          ) : (
            <button
              className="auth-button"
              type="button"
              disabled={isAuthLoading}
              onClick={onOpenAuth}
            >
              {isAuthLoading ? 'Đang kiểm tra…' : 'Đăng nhập'}
            </button>
          )}
        </div>
      </div>
    </header>
  )
}

function LegalArticlePage({
  articleCode,
  navAuth,
}: {
  articleCode: string
  navAuth: NavAuthProps
}) {
  const [article, setArticle] = useState<LegalArticleResponse | null>(null)
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    async function loadArticle() {
      setError('')
      setIsLoading(true)
      try {
        const response = await fetch(
          `${API_BASE_URL}/sources/${encodeURIComponent(articleCode)}`,
          { signal: controller.signal },
        )
        if (!response.ok) throw new Error(await readError(response))
        setArticle((await response.json()) as LegalArticleResponse)
      } catch (requestError) {
        if (requestError instanceof DOMException && requestError.name === 'AbortError') {
          return
        }
        setError(requestError instanceof Error
          ? requestError.message
          : 'Không thể tải nội dung điều luật.')
      } finally {
        if (!controller.signal.aborted) setIsLoading(false)
      }
    }
    void loadArticle()
    return () => controller.abort()
  }, [articleCode])

  const hierarchy = article
    ? [
        article.topic_code && article.topic_name
          ? `Đề mục ${article.topic_code} — ${article.topic_name}`
          : null,
        article.chapter_number && article.chapter_title
          ? `Chương ${article.chapter_number} — ${article.chapter_title}`
          : null,
        article.section_number && article.section_title
          ? `Mục ${article.section_number} — ${article.section_title}`
          : null,
      ].filter((item): item is string => Boolean(item))
    : []

  return (
    <div className="app-shell source-page-shell">
      <TopNav activeTab="source" showBackLink {...navAuth} />

      <main className="source-page-main">
        {isLoading && (
          <section className="source-page-state" aria-live="polite">
            <span className="large-spinner" aria-hidden="true" />
            <h1>Đang tải điều luật…</h1>
          </section>
        )}

        {error && !isLoading && (
          <section className="source-page-state source-page-error" role="alert">
            <p className="eyebrow">Không thể mở nguồn</p>
            <h1>{error}</h1>
            <a className="back-link" href="/">Quay lại trang hỏi đáp</a>
          </section>
        )}

        {article && !isLoading && !error && (
          <article className="source-document">
            <header className="source-document-header">
              <div>
                <p className="eyebrow">Văn bản trong dữ liệu cục bộ</p>
                <h1>
                  {article.article_number ? `Điều ${article.article_number}` : article.article_code}
                  {article.article_title ? ` — ${article.article_title}` : ''}
                </h1>
                <p className="source-document-citation">{article.citation_label}</p>
              </div>
              {article.official_url && (
                <a
                  className="official-source-button"
                  href={article.official_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  Mở văn bản chính thức ↗
                </a>
              )}
            </header>

            <dl className="article-meta">
              <div><dt>Mã pháp điển</dt><dd>{article.article_code}</dd></div>
              <div>
                <dt>Văn bản</dt>
                <dd>{article.document_number || article.document_title || 'Chưa xác định'}</dd>
              </div>
              <div><dt>Nguồn ghép</dt><dd>{article.chunk_count} đoạn dữ liệu</dd></div>
              <div>
                <dt>Kiểm tra liên kết</dt>
                <dd>{article.url_last_checked_at || 'Chưa ghi nhận'}</dd>
              </div>
            </dl>

            {hierarchy.length > 0 && (
              <div className="article-hierarchy">
                {hierarchy.map((item) => <p key={item}>{item}</p>)}
              </div>
            )}

            <div className="legal-unit-list">
              {article.units.map((unit) => (
                <section
                  className={`legal-unit ${unit.unit_type === 'table' ? 'is-table' : ''}`}
                  key={unit.chunk_id}
                >
                  <h2>{unit.label}</h2>
                  <p>{unit.content}</p>
                </section>
              ))}
            </div>

            {article.source_note_text && (
              <aside className="source-note">
                <strong>Ghi chú nguồn</strong>
                <p>{article.source_note_text}</p>
              </aside>
            )}
          </article>
        )}
      </main>

      <footer>
        <p>Nội dung được hiển thị từ chính corpus dùng để truy hồi và sinh đáp án.</p>
      </footer>
    </div>
  )
}

function storedMessageToAnswer(message: StoredMessage): DisplayAnswer {
  return {
    id: message.id,
    content: message.content,
    method: message.retrieval_method,
    sources: message.sources,
    retrievalMs: message.retrieval_ms,
    generationMs: message.generation_ms,
    totalMs: message.total_ms,
    model: message.model,
    insufficientEvidence: message.insufficient_evidence,
    outOfScope: message.out_of_scope,
    generationFailed: message.generation_failed,
    bookmarked: message.bookmarked,
  }
}

function transientResultToAnswer(result: AskResponse): DisplayAnswer {
  return {
    id: result.assistant_message_id,
    content: result.answer,
    method: result.method,
    sources: result.sources,
    retrievalMs: result.retrieval_ms,
    generationMs: result.generation_ms,
    totalMs: result.total_ms,
    model: result.model,
    insufficientEvidence: result.insufficient_evidence,
    outOfScope: result.out_of_scope,
    generationFailed: result.generation_failed,
    bookmarked: false,
    historyWarning: result.history_error,
  }
}

type RetrievalDropdownProps = {
  value: RetrievalMethod
  onChange: (method: RetrievalMethod) => void
  disabled?: boolean
}

function RetrievalDropdown({ value, onChange, disabled }: RetrievalDropdownProps) {
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setIsOpen(false)
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleKeyDown)
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen])

  const labelMap: Record<RetrievalMethod, string> = {
    hybrid: 'Kết hợp',
    dense: 'Ngữ nghĩa',
    sparse: 'Từ khóa',
  }

  const optionDetails: Record<
    RetrievalMethod,
    { title: string; subtitle: string; badge?: string }
  > = {
    hybrid: {
      title: 'Kết hợp',
      subtitle: 'Hybrid · Từ khóa + ngữ nghĩa',
      badge: 'Khuyến nghị',
    },
    dense: {
      title: 'Ngữ nghĩa',
      subtitle: 'Dense · Truy hồi theo ý nghĩa',
    },
    sparse: {
      title: 'Từ khóa',
      subtitle: 'Sparse · Khớp từ và cụm từ chính xác',
    },
  }

  return (
    <div className="retrieval-dropdown-container" ref={dropdownRef}>
      <button
        type="button"
        className="retrieval-dropdown-trigger"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-label="Chọn phương pháp truy hồi"
        disabled={disabled}
        onClick={() => setIsOpen((prev) => !prev)}
      >
        <span className="retrieval-trigger-text">{labelMap[value]}</span>
        <svg
          className={`retrieval-chevron ${isOpen ? 'open' : ''}`}
          viewBox="0 0 20 20"
          fill="currentColor"
          width="14"
          height="14"
          aria-hidden="true"
        >
          <path
            fillRule="evenodd"
            d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
            clipRule="evenodd"
          />
        </svg>
      </button>

      {isOpen && (
        <div className="retrieval-dropdown-menu" role="listbox" aria-label="Phương pháp truy hồi">
          {methods.map((item) => {
            const detail = optionDetails[item.value]
            const isSelected = value === item.value
            return (
              <button
                key={item.value}
                type="button"
                role="option"
                aria-selected={isSelected}
                className={`retrieval-dropdown-option ${isSelected ? 'selected' : ''}`}
                onClick={() => {
                  onChange(item.value)
                  setIsOpen(false)
                }}
              >
                <div className="retrieval-option-header">
                  <span className="retrieval-option-title">{detail.title}</span>
                  {detail.badge && <span className="retrieval-option-badge">{detail.badge}</span>}
                </div>
                <span className="retrieval-option-subtitle">{detail.subtitle}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

function QuestionAnswerPage({
  authUser,
  isAuthLoading,
  onOpenAuth,
  onLogout,
}: NavAuthProps) {
  const [question, setQuestion] = useState('')
  const [method, setMethod] = useState<RetrievalMethod>('hybrid')
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [activeConversationId, setActiveId] = useState<string | null>(
    () => getActiveConversationId(),
  )
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(null)
  const [transientResult, setTransientResult] = useState<AskResponse | null>(null)
  const [transientQuestion, setTransientQuestion] = useState('')
  const [error, setError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isHistoryLoading, setIsHistoryLoading] = useState(Boolean(authUser))
  const [isConversationLoading, setIsConversationLoading] = useState(false)
  const [busyBookmarkId, setBusyBookmarkId] = useState<string | null>(null)
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && isSidebarOpen) {
        setIsSidebarOpen(false)
      }
    }
    if (isSidebarOpen) {
      window.addEventListener('keydown', handleKeyDown)
    }
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isSidebarOpen])

  useEffect(() => {
    if (!authUser) return
    const controller = new AbortController()
    async function loadInitialHistory() {
      setIsHistoryLoading(true)
      setHistoryError('')
      try {
        const items = await fetchConversations(controller.signal)
        setConversations(items)
        const requestedId = getActiveConversationId()
        if (requestedId && items.some((item) => item.id === requestedId)) {
          setIsConversationLoading(true)
          const detail = await fetchConversation(requestedId, controller.signal)
          setActiveConversation(detail)
          setActiveId(requestedId)
        } else {
          setActiveConversationId(null)
          setActiveId(null)
        }
      } catch (requestError) {
        if (requestError instanceof DOMException && requestError.name === 'AbortError') return
        // Do not let an unavailable history database block the primary Q&A flow.
        // A new answer can still be generated and returned with history_saved=false.
        setActiveConversationId(null)
        setActiveId(null)
        setActiveConversation(null)
        setHistoryError(requestError instanceof Error
          ? requestError.message
          : 'Không thể tải lịch sử hội thoại.')
      } finally {
        if (!controller.signal.aborted) {
          setIsHistoryLoading(false)
          setIsConversationLoading(false)
        }
      }
    }
    void loadInitialHistory()
    return () => controller.abort()
  }, [authUser])

  async function reloadConversation(conversationId: string) {
    const [detail, items] = await Promise.all([
      fetchConversation(conversationId),
      fetchConversations(),
    ])
    setActiveConversation(detail)
    setConversations(items)
    setHistoryError('')
    setActiveId(conversationId)
    setActiveConversationId(conversationId)
    return detail
  }

  async function selectConversation(conversationId: string) {
    setError('')
    setHistoryError('')
    setTransientResult(null)
    setTransientQuestion('')
    setIsConversationLoading(true)
    try {
      await reloadConversation(conversationId)
    } catch (requestError) {
      setHistoryError(requestError instanceof Error
        ? requestError.message
        : 'Không thể mở hội thoại.')
    } finally {
      setIsConversationLoading(false)
    }
  }

  function startNewConversation() {
    setActiveId(null)
    setActiveConversation(null)
    setActiveConversationId(null)
    setTransientResult(null)
    setTransientQuestion('')
    setError('')
    setQuestion('')
  }

  async function handleRename(conversationId: string, nextTitle: string) {
    setHistoryError('')
    const detail = await renameConversation(conversationId, nextTitle)
    const items = await fetchConversations()
    setConversations(items)
    if (activeConversationId === conversationId) {
      setActiveConversation(detail)
    }
  }

  async function handleDelete(conversation: ConversationSummary) {
    if (!window.confirm(`Xóa hội thoại “${conversation.title}”?`)) return
    setHistoryError('')
    try {
      await deleteConversation(conversation.id)
      const items = await fetchConversations()
      setConversations(items)
      if (activeConversationId === conversation.id) startNewConversation()
    } catch (requestError) {
      setHistoryError(requestError instanceof Error
        ? requestError.message
        : 'Không thể xóa hội thoại.')
    }
  }

  async function handleToggleBookmark(messageId: string, shouldSave: boolean) {
    setBusyBookmarkId(messageId)
    setHistoryError('')
    try {
      if (shouldSave) await saveBookmark(messageId)
      else await deleteBookmark(messageId)
      if (activeConversationId) await reloadConversation(activeConversationId)
    } catch (requestError) {
      setHistoryError(requestError instanceof Error
        ? requestError.message
        : 'Không thể cập nhật đánh dấu.')
    } finally {
      setBusyBookmarkId(null)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalizedQuestion = question.trim()
    if (normalizedQuestion.length < 3) {
      setError('Vui lòng nhập câu hỏi có ít nhất 3 ký tự.')
      return
    }

    setError('')
    setHistoryError('')
    setTransientResult(null)
    setTransientQuestion(normalizedQuestion)
    setIsLoading(true)
    try {
      const response = await sessionFetch('/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: normalizedQuestion,
          method,
          conversation_id: authUser ? activeConversationId : null,
        }),
      }, Boolean(authUser))
      if (!response.ok) throw new Error(await readError(response))
      const nextResult = await response.json() as AskResponse
      setTransientResult(nextResult)
      setQuestion('')

      if (nextResult.history_saved && nextResult.conversation_id) {
        try {
          await reloadConversation(nextResult.conversation_id)
          setTransientResult(null)
          setTransientQuestion('')
        } catch (historyRequestError) {
          setHistoryError(historyRequestError instanceof Error
            ? historyRequestError.message
            : 'Đã trả lời nhưng chưa tải lại được lịch sử.')
        }
      }
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Không thể kết nối đến hệ thống.'
      setTransientQuestion('')
      setError(message === 'Failed to fetch'
        ? 'Không kết nối được backend tại cổng 8000. Hãy kiểm tra API đang chạy.'
        : message)
    } finally {
      setIsLoading(false)
    }
  }

  const hasTranscript = Boolean(activeConversation?.messages.length || transientResult)

  return (
    <div className="app-shell qa-app-shell">
      <TopNav
        activeTab="qa"
        authUser={authUser}
        isAuthLoading={isAuthLoading}
        onOpenAuth={onOpenAuth}
        onLogout={onLogout}
      />

      <aside className={`app-sidebar qa-sidebar-wrapper ${isSidebarOpen ? 'open' : ''}`}>
        <ConversationSidebar
          conversations={conversations}
          activeConversationId={activeConversationId}
          isLoading={isHistoryLoading}
          isBusy={isHistoryLoading || isLoading || isConversationLoading}
          error={historyError}
          isAuthenticated={Boolean(authUser)}
          onLogin={onOpenAuth}
          onNew={() => {
            startNewConversation()
            setIsSidebarOpen(false)
          }}
          onSelect={(conversationId) => {
            void selectConversation(conversationId)
            setIsSidebarOpen(false)
          }}
          onRename={(conversationId, title) => handleRename(conversationId, title)}
          onDelete={(conversation) => void handleDelete(conversation)}
        />
      </aside>

      {isSidebarOpen && (
        <div
          className="sidebar-backdrop"
          onClick={() => setIsSidebarOpen(false)}
          aria-hidden="true"
        />
      )}

      <main className="chat-main qa-main-column">
        <header className="chat-header">
          <button
            type="button"
            className="sidebar-toggle-btn"
            onClick={() => setIsSidebarOpen((prev) => !prev)}
            aria-expanded={isSidebarOpen}
            aria-label="Mở lịch sử hội thoại"
          >
            <svg
              viewBox="0 0 24 24"
              width="20"
              height="20"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>
          <h1 className="chat-header-title">
            {activeConversation?.title || 'Hỏi đáp pháp luật lao động'}
          </h1>
        </header>

        <section className="chat-scroll-area">
          <div className="chat-content-column chat-content-container">
            {!hasTranscript && !error && !isLoading && !isConversationLoading && (
              <div className="chat-empty-state">
                <div className="empty-icon" aria-hidden="true">§</div>
                <h2>Bạn cần tra cứu vấn đề gì?</h2>
                <p>Hỏi đáp pháp luật lao động Việt Nam có căn cứ, điều luật trích dẫn chính xác.</p>
                <div className="suggestion-cards" aria-label="Câu hỏi gợi ý">
                  {examples.map((example) => (
                    <button
                      type="button"
                      key={example}
                      className="suggestion-card"
                      onClick={() => setQuestion(example)}
                    >
                      <span className="suggestion-text">{example}</span>
                      <span className="suggestion-arrow" aria-hidden="true">→</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {(isLoading || isConversationLoading) && !hasTranscript && (
              <div className="chat-loading-state">
                <span className="large-spinner" aria-hidden="true" />
                <h2>{isLoading ? 'Đang tìm căn cứ pháp lý…' : 'Đang mở hội thoại…'}</h2>
                <p>Hệ thống đang tìm và đối chiếu các căn cứ phù hợp.</p>
              </div>
            )}

            {error && (
              <div className="error-state" role="alert">
                <strong>Chưa thể trả lời</strong>
                <p>{error}</p>
              </div>
            )}

            {!isConversationLoading && hasTranscript && (
              <div className="transcript">
                {activeConversation?.messages.map((message) => (
                  message.role === 'user' ? (
                    <div className="transcript-question" key={message.id}>
                      <p>{message.content}</p>
                    </div>
                  ) : (
                    <AnswerCard
                      key={message.id}
                      answer={storedMessageToAnswer(message)}
                      isBookmarkBusy={busyBookmarkId === message.id}
                      onToggleBookmark={authUser
                        ? (messageId, shouldSave) => {
                            void handleToggleBookmark(messageId, shouldSave)
                          }
                        : undefined}
                    />
                  )
                ))}
                {transientResult && transientQuestion && (
                  <div className="transcript-question transient" key="transient-question">
                    <p>{transientQuestion}</p>
                  </div>
                )}
                {isLoading && (
                  <div className="chat-inline-loading">
                    <span className="large-spinner" aria-hidden="true" />
                    <span>Đang tra cứu và tổng hợp đáp án…</span>
                  </div>
                )}
                {transientResult && (
                  <AnswerCard answer={transientResultToAnswer(transientResult)} />
                )}
              </div>
            )}
          </div>
        </section>

        <footer className="chat-composer-area composer-sticky-wrapper">
          <form className="chat-composer" onSubmit={handleSubmit}>
            <div className="composer-inner">
              <textarea
                id="question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    if (question.trim().length >= 3 && !isLoading) {
                      e.currentTarget.form?.requestSubmit()
                    }
                  }
                }}
                placeholder="Hỏi đáp về Luật Lao động Việt Nam..."
                maxLength={2000}
                rows={1}
              />

              <div className="composer-footer-bar">
                <RetrievalDropdown
                  value={method}
                  onChange={setMethod}
                  disabled={isLoading}
                />

                <div className="composer-actions-right">
                  <span className="character-count">{question.length}/2000</span>
                  <button
                    className="composer-submit-btn"
                    type="submit"
                    disabled={isLoading || question.trim().length < 3}
                    aria-label="Gửi câu hỏi"
                  >
                    {isLoading ? (
                      <span className="spinner" aria-hidden="true" />
                    ) : (
                      <svg
                        viewBox="0 0 24 24"
                        width="18"
                        height="18"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        aria-hidden="true"
                      >
                        <line x1="12" y1="19" x2="12" y2="5" />
                        <polyline points="5 12 12 5 19 12" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>
            </div>

            <p className="composer-disclaimer">
              Công cụ hỗ trợ tra cứu học thuật, không thay thế tư vấn pháp lý chuyên môn.
            </p>
          </form>
        </footer>
      </main>
    </div>
  )
}

type Route =
  | { type: 'qa' }
  | { type: 'source'; articleCode: string }
  | { type: 'library-list' }
  | { type: 'library-detail'; documentId: string }
  | { type: 'saved' }

function parseRoute(pathname: string): Route {
  const sourceMatch = pathname.match(/^\/sources\/([^/]+)\/?$/)
  if (sourceMatch) {
    try {
      return { type: 'source', articleCode: decodeURIComponent(sourceMatch[1]) }
    } catch {
      return { type: 'source', articleCode: sourceMatch[1] }
    }
  }

  const docMatch = pathname.match(/^\/library\/([^/]+)\/?$/)
  if (docMatch) {
    try {
      return { type: 'library-detail', documentId: decodeURIComponent(docMatch[1]) }
    } catch {
      return { type: 'library-detail', documentId: docMatch[1] }
    }
  }

  if (pathname === '/saved' || pathname.startsWith('/saved/')) {
    return { type: 'saved' }
  }

  if (pathname === '/library' || pathname.startsWith('/library/')) {
    return { type: 'library-list' }
  }

  return { type: 'qa' }
}

function App() {
  const [currentPath, setCurrentPath] = useState(window.location.pathname)
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [isAuthLoading, setIsAuthLoading] = useState(true)
  const [showAuthDialog, setShowAuthDialog] = useState(false)
  const [authNotice, setAuthNotice] = useState('')

  useEffect(() => {
    const handlePopState = () => setCurrentPath(window.location.pathname)
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  useEffect(() => {
    function handleExpiredSession() {
      setAuthUser(null)
      setActiveConversationId(null)
      setAuthNotice('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.')
      navigateTo('/')
    }
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, handleExpiredSession)
    return () => {
      window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, handleExpiredSession)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    async function restoreSession() {
      try {
        setAuthUser(await fetchCurrentUser(controller.signal))
      } catch (requestError) {
        if (requestError instanceof DOMException && requestError.name === 'AbortError') return
        setAuthNotice(requestError instanceof Error
          ? requestError.message
          : 'Không thể kiểm tra phiên đăng nhập.')
      } finally {
        if (!controller.signal.aborted) setIsAuthLoading(false)
      }
    }
    void restoreSession()
    return () => controller.abort()
  }, [])

  function handleAuthenticated(result: AuthResult) {
    setAuthUser(result.user)
    setShowAuthDialog(false)
    setActiveConversationId(null)
    setAuthNotice(result.migrated_anonymous_history
      ? 'Đăng nhập thành công. Lịch sử ẩn danh trên trình duyệt này đã được chuyển vào tài khoản.'
      : 'Đăng nhập thành công.')
  }

  async function handleLogout() {
    setIsAuthLoading(true)
    try {
      await logoutAccount()
      setAuthUser(null)
      setActiveConversationId(null)
      setAuthNotice('Đã đăng xuất khỏi tài khoản.')
      navigateTo('/')
    } catch (requestError) {
      setAuthNotice(requestError instanceof Error
        ? requestError.message
        : 'Không thể đăng xuất.')
    } finally {
      setIsAuthLoading(false)
    }
  }

  const navAuth: NavAuthProps = {
    authUser,
    isAuthLoading,
    onOpenAuth: () => setShowAuthDialog(true),
    onLogout: () => void handleLogout(),
  }
  const route = parseRoute(currentPath)

  let page: ReactNode
  if (route.type === 'source') {
    page = <LegalArticlePage articleCode={route.articleCode} navAuth={navAuth} />
  } else if (route.type === 'library-detail') {
    page = (
      <div className="app-shell">
        <TopNav activeTab="library" {...navAuth} />
        <main>
          <DocumentDetailPage
            key={route.documentId}
            documentId={route.documentId}
            onBack={() => navigateTo('/library')}
          />
        </main>
        <footer>
          <p>Công cụ hỗ trợ tra cứu học thuật, không thay thế tư vấn pháp lý chuyên môn.</p>
        </footer>
      </div>
    )
  } else if (route.type === 'saved') {
    page = (
      <div className="app-shell">
        <TopNav activeTab="saved" {...navAuth} />
        <main>
          {authUser ? (
            <SavedAnswersPage
              key={authUser.id}
              onOpenConversation={(conversationId) => {
                setActiveConversationId(conversationId)
                navigateTo('/')
              }}
            />
          ) : (
            <section className="auth-required">
              <p className="eyebrow">Dữ liệu cá nhân</p>
              <h1>Cần đăng nhập để xem câu trả lời đã lưu</h1>
              <p>Bookmark được lưu trong PostgreSQL và chỉ tài khoản sở hữu mới đọc được.</p>
              <button className="auth-button" type="button" onClick={navAuth.onOpenAuth}>
                Đăng nhập
              </button>
            </section>
          )}
        </main>
        <footer>
          <p>Công cụ hỗ trợ tra cứu học thuật, không thay thế tư vấn pháp lý chuyên môn.</p>
        </footer>
      </div>
    )
  } else if (route.type === 'library-list') {
    page = (
      <div className="app-shell">
        <TopNav activeTab="library" {...navAuth} />
        <main>
          <DocumentLibraryPage
            onSelectDocument={(docId) => navigateTo(`/library/${encodeURIComponent(docId)}`)}
          />
        </main>
        <footer>
          <p>Công cụ hỗ trợ tra cứu học thuật, không thay thế tư vấn pháp lý chuyên môn.</p>
        </footer>
      </div>
    )
  } else {
    page = (
      <QuestionAnswerPage
        key={authUser?.id ?? 'anonymous'}
        {...navAuth}
      />
    )
  }

  return (
    <>
      {page}
      {authNotice && (
        <div className="auth-toast" role="status">
          <span>{authNotice}</span>
          <button type="button" aria-label="Đóng thông báo" onClick={() => setAuthNotice('')}>
            ×
          </button>
        </div>
      )}
      {showAuthDialog && (
        <AuthDialog
          onClose={() => setShowAuthDialog(false)}
          onAuthenticated={handleAuthenticated}
        />
      )}
    </>
  )
}

export default App
