import { useState } from 'react'
import Markdown from 'react-markdown'
import type { LegalSource, RetrievalMethod } from '../../api/qaTypes'
import { CitationList } from '../../components/CitationList'

export type DisplayAnswer = {
  id: string | null
  content: string
  method: RetrievalMethod | null
  sources: LegalSource[]
  retrievalMs: number | null
  generationMs: number | null
  totalMs: number | null
  model: string | null
  insufficientEvidence: boolean
  outOfScope: boolean
  generationFailed: boolean
  bookmarked: boolean
  historyWarning?: string | null
}

function formatMs(value: number | null) {
  if (value === null) return 'Không ghi nhận'
  if (value >= 1000) return `${(value / 1000).toFixed(1).replace('.', ',')} giây`
  return `${Math.round(value)} ms`
}

function methodLabel(method: RetrievalMethod | null) {
  if (!method) return 'Đã lưu'
  if (method === 'hybrid') return 'Kết hợp'
  if (method === 'dense') return 'Ngữ nghĩa'
  if (method === 'sparse') return 'Từ khóa'
  return method
}

export function AnswerCard({
  answer,
  isBookmarkBusy = false,
  onToggleBookmark,
}: {
  answer: DisplayAnswer
  isBookmarkBusy?: boolean
  onToggleBookmark?: (messageId: string, shouldSave: boolean) => void
}) {
  const [isProcessingOpen, setIsProcessingOpen] = useState(false)

  const hasMetrics =
    answer.retrievalMs !== null ||
    answer.generationMs !== null ||
    answer.totalMs !== null ||
    Boolean(answer.model)

  return (
    <article className="answer-stack stored-answer">
      <div className="answer-header">
        <div>
          <p className="eyebrow">Kết quả · {methodLabel(answer.method)}</p>
          <h2>Câu trả lời</h2>
        </div>
        <div className="answer-header-actions">
          {answer.outOfScope ? (
            <span className="warning-badge">Ngoài phạm vi</span>
          ) : answer.generationFailed ? (
            <span className="warning-badge">Lỗi sinh đáp án</span>
          ) : answer.insufficientEvidence ? (
            <span className="warning-badge">Thiếu căn cứ</span>
          ) : null}
          {answer.id && onToggleBookmark && (
            <button
              type="button"
              className={`bookmark-button ${answer.bookmarked ? 'active' : ''}`}
              disabled={isBookmarkBusy}
              aria-pressed={answer.bookmarked}
              onClick={() => onToggleBookmark(answer.id as string, !answer.bookmarked)}
            >
              {answer.bookmarked ? '★ Đã lưu' : '☆ Đánh dấu'}
            </button>
          )}
        </div>
      </div>

      {answer.historyWarning && (
        <p className="history-save-warning" role="status">{answer.historyWarning}</p>
      )}

      <div className="answer-text">
        <Markdown
          components={{
            a: ({ children, ...props }) => (
              <a {...props} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            ),
          }}
        >
          {answer.content}
        </Markdown>
      </div>

      <CitationList sources={answer.sources} />

      {hasMetrics && (
        <div className="processing-details-section">
          <button
            type="button"
            className="processing-details-trigger"
            onClick={() => setIsProcessingOpen((prev) => !prev)}
            aria-expanded={isProcessingOpen}
          >
            <span>
              Chi tiết xử lý · {methodLabel(answer.method)}
              {answer.totalMs !== null ? ` · ${formatMs(answer.totalMs)}` : ''}
            </span>
            <span className={`processing-chevron ${isProcessingOpen ? 'open' : ''}`} aria-hidden="true">
              ▼
            </span>
          </button>

          {isProcessingOpen && (
            <dl className="metrics processing-metrics-grid">
              <div><dt>Truy hồi</dt><dd>{formatMs(answer.retrievalMs)}</dd></div>
              <div><dt>Sinh đáp án</dt><dd>{formatMs(answer.generationMs)}</dd></div>
              <div><dt>Tổng cộng</dt><dd>{formatMs(answer.totalMs)}</dd></div>
              <div><dt>Mô hình</dt><dd title={answer.model || ''}>{answer.model || 'Không gọi LLM'}</dd></div>
            </dl>
          )}
        </div>
      )}
    </article>
  )
}
