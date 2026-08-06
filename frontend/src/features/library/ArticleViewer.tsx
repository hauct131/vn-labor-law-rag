import { useEffect, useRef, useState } from 'react'
import type { LegalArticleResponse } from '../../api/legalArticleTypes'
import { fetchArticleSource } from './documentLibraryApi'

interface ArticleViewerProps {
  articleCode: string
  onClose: () => void
}

export function ArticleViewer({ articleCode, onClose }: ArticleViewerProps) {
  const [article, setArticle] = useState<LegalArticleResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    closeButtonRef.current?.focus()
  }, [])

  useEffect(() => {
    const controller = new AbortController()

    async function loadArticle() {
      setIsLoading(true)
      setError('')
      try {
        const data = await fetchArticleSource(articleCode, controller.signal)
        setArticle(data)
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          return
        }
        setError(
          err instanceof Error
            ? err.message
            : 'Không thể tải nội dung điều luật.',
        )
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false)
        }
      }
    }

    void loadArticle()

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)

    return () => {
      controller.abort()
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [articleCode, onClose])

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
    <div
      className="article-viewer-backdrop"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="article-viewer-title"
    >
      <div
        className="article-viewer-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="article-viewer-header">
          <div>
            <p className="article-viewer-eyebrow">Chi tiết điều luật</p>
            <h2 id="article-viewer-title" className="article-viewer-title">
              {article?.article_title
                ? `Điều ${article.article_number || ''}: ${article.article_title}`
                : article?.citation_label || articleCode}
            </h2>
          </div>
          <button
            ref={closeButtonRef}
            autoFocus
            type="button"
            className="article-viewer-close"
            onClick={onClose}
            aria-label="Đóng bảng xem điều luật"
          >
            ✕ Đóng
          </button>
        </header>

        <div className="article-viewer-body" aria-live="polite">
          {isLoading && (
            <div className="article-viewer-loading">
              <span className="spinner" />
              <span>Đang tải nội dung điều luật...</span>
            </div>
          )}

          {error && (
            <div className="article-viewer-error" role="alert">
              <p className="error-title">Lỗi tải dữ liệu</p>
              <p className="error-body">{error}</p>
            </div>
          )}

          {!isLoading && !error && article && (
            <>
              <div className="article-viewer-citation-box">
                <p className="citation-text">{article.citation_label}</p>
                <div className="citation-meta">
                  <span>Mã điều: {article.article_code}</span>
                  {article.document_number && (
                    <span>Số hiệu: {article.document_number}</span>
                  )}
                  {article.chunk_count > 0 && (
                    <span>Gồm {article.chunk_count} đoạn luật</span>
                  )}
                </div>
              </div>

              {hierarchy.length > 0 && (
                <div className="article-viewer-hierarchy">
                  <p className="hierarchy-label">Vị trí trong văn bản:</p>
                  <ul>
                    {hierarchy.map((item, idx) => (
                      <li key={idx}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              {article.source_note_text && (
                <div className="article-viewer-note">
                  <strong>Ghi chú văn bản:</strong> {article.source_note_text}
                </div>
              )}

              <div className="article-viewer-units">
                <h3 className="units-heading">Nội dung chi tiết</h3>
                {article.units.length === 0 ? (
                  <p className="empty-text">Chưa có nội dung chi tiết cho điều luật này.</p>
                ) : (
                  article.units.map((unit) => (
                    <article className="unit-card" key={unit.chunk_id}>
                      <h4 className="unit-label">{unit.label}</h4>
                      <p className="unit-content">{unit.content}</p>
                    </article>
                  ))
                )}
              </div>

              {article.official_url && (
                <div className="article-viewer-footer">
                  <a
                    href={article.official_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="official-url-link"
                  >
                    Xem văn bản gốc trên Cổng Thông tin điện tử Chính phủ ↗
                  </a>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
