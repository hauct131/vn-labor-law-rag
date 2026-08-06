import Markdown from 'react-markdown'
import type { LegalSource, RetrievalMethod } from '../../api/qaTypes'

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
  if (value >= 1000) return `${(value / 1000).toFixed(2)} giây`
  return `${Math.round(value)} ms`
}

function sourceLocation(source: LegalSource) {
  const parts: string[] = []
  if (source.clause_number) parts.push(`Khoản ${source.clause_number}`)
  if (source.point_labels.length) parts.push(`Điểm ${source.point_labels.join(', ')}`)
  return parts.join(' · ')
}

function sourceTypeLabel(sourceType: string | null) {
  if (sourceType === 'LQ') return 'Bộ luật/Luật'
  if (sourceType === 'NĐ') return 'Nghị định'
  if (sourceType === 'TT') return 'Thông tư'
  return sourceType
}

function articlePagePath(articleCode: string) {
  return `/sources/${encodeURIComponent(articleCode)}`
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
  return (
    <article className="answer-stack stored-answer">
      <div className="answer-header">
        <div>
          <p className="eyebrow">Kết quả · {answer.method || 'đã lưu'}</p>
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

      <dl className="metrics">
        <div><dt>Truy hồi</dt><dd>{formatMs(answer.retrievalMs)}</dd></div>
        <div><dt>Sinh đáp án</dt><dd>{formatMs(answer.generationMs)}</dd></div>
        <div><dt>Tổng cộng</dt><dd>{formatMs(answer.totalMs)}</dd></div>
        <div><dt>Mô hình</dt><dd title={answer.model || ''}>{answer.model || 'Không gọi LLM'}</dd></div>
      </dl>

      <div className="sources-heading">
        <h3>Nguồn đối chiếu</h3>
        <span>{answer.sources.length} đoạn luật</span>
      </div>

      <div className="source-list">
        {answer.sources.map((source, index) => (
          <article className="source-card" key={`${source.chunk_id}-${index}`}>
            <div className="source-topline">
              <span className="source-index">{source.source_id || `S${index + 1}`}</span>
              {source.source_type && (
                <span className="source-type">{sourceTypeLabel(source.source_type)}</span>
              )}
            </div>
            <h4 className="source-citation">
              {source.citation_label || source.article_code || source.chunk_id}
            </h4>
            {source.article_title && (
              <p className="source-article-title">{source.article_title}</p>
            )}
            {sourceLocation(source) && (
              <p className="source-location">{sourceLocation(source)}</p>
            )}
            {source.article_code && (
              <p className="source-code">Mã pháp điển: {source.article_code}</p>
            )}
            <p className="source-content">{source.content}</p>
            <div className="source-footer">
              <span>
                Hạng {source.rank}
                {source.score !== null ? ` · score ${source.score.toFixed(4)}` : ''}
              </span>
              <span className="source-actions">
                {source.article_code && (
                  <a
                    href={articlePagePath(source.article_code)}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    Xem đầy đủ điều luật
                  </a>
                )}
                {source.source_url && (
                  <a
                    href={source.source_url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    Văn bản chính thức ↗
                  </a>
                )}
              </span>
            </div>
          </article>
        ))}
      </div>
    </article>
  )
}
