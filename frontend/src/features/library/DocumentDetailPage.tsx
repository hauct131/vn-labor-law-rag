import { type FormEvent, useEffect, useState } from 'react'
import { ArticleViewer } from './ArticleViewer'
import { fetchDocument, fetchDocumentArticles } from './documentLibraryApi'
import type {
  ArticleListResponse,
  DocumentDetail,
} from './documentLibraryTypes'

interface DocumentDetailPageProps {
  documentId: string
  onBack: () => void
}

function sourceTypeLabel(sourceType: string | null) {
  if (sourceType === 'LQ') return 'Bộ luật/Luật'
  if (sourceType === 'NĐ') return 'Nghị định'
  if (sourceType === 'TT') return 'Thông tư'
  return sourceType || 'Văn bản'
}

export function DocumentDetailPage({
  documentId,
  onBack,
}: DocumentDetailPageProps) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null)
  const [isDocLoading, setIsDocLoading] = useState(true)
  const [docError, setDocError] = useState('')

  const [articlesData, setArticlesData] = useState<ArticleListResponse | null>(null)
  const [isArticlesLoading, setIsArticlesLoading] = useState(true)
  const [articlesError, setArticlesError] = useState('')

  const [searchInput, setSearchInput] = useState('')
  const [activeQuery, setActiveQuery] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 20

  const [selectedArticleCode, setSelectedArticleCode] = useState<string | null>(null)



  // Load document details
  useEffect(() => {
    const controller = new AbortController()

    async function loadDoc() {
      setIsDocLoading(true)
      setDocError('')
      try {
        const detail = await fetchDocument(documentId, controller.signal)
        setDoc(detail)
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setDocError(
          err instanceof Error
            ? err.message
            : 'Không thể tải thông tin chi tiết văn bản.',
        )
      } finally {
        if (!controller.signal.aborted) {
          setIsDocLoading(false)
        }
      }
    }

    void loadDoc()
    return () => controller.abort()
  }, [documentId])

  // Load document articles list
  useEffect(() => {
    const controller = new AbortController()

    async function loadArticles() {
      setIsArticlesLoading(true)
      setArticlesError('')
      try {
        const res = await fetchDocumentArticles(
          documentId,
          {
            q: activeQuery,
            page,
            page_size: pageSize,
          },
          controller.signal,
        )
        setArticlesData(res)
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setArticlesError(
          err instanceof Error
            ? err.message
            : 'Không thể tải danh sách điều luật.',
        )
      } finally {
        if (!controller.signal.aborted) {
          setIsArticlesLoading(false)
        }
      }
    }

    void loadArticles()
    return () => controller.abort()
  }, [documentId, activeQuery, page])

  const handleSearchSubmit = (e: FormEvent) => {
    e.preventDefault()
    setActiveQuery(searchInput)
    setPage(1)
  }

  const pagination = articlesData?.pagination

  return (
    <div className="library-detail-shell">
      <header className="library-detail-topbar">
        <button
          type="button"
          onClick={onBack}
          className="back-button"
        >
          ← Quay lại thư viện pháp luật
        </button>
      </header>

      {isDocLoading && (
        <div className="state-panel loading-state" aria-live="polite">
          <span className="spinner" />
          <span>Đang tải thông tin chi tiết văn bản...</span>
        </div>
      )}

      {docError && (
        <div className="state-panel error-state" role="alert">
          <p className="error-title">Không thể tải thông tin văn bản</p>
          <p className="error-body">{docError}</p>
          <button type="button" onClick={onBack} className="btn-secondary">
            Trở về danh sách văn bản
          </button>
        </div>
      )}

      {!isDocLoading && !docError && doc && (
        <section className="document-header-card">
          <div className="doc-header-main">
            <div className="doc-badges">
              {doc.source_type && (
                <span className="badge badge-type">
                  {sourceTypeLabel(doc.source_type)}
                </span>
              )}
              <span className="badge badge-count">
                {doc.article_count} điều luật
              </span>
            </div>
            <h1 className="doc-title">{doc.title || doc.document_number}</h1>
            <p className="doc-number">Số hiệu: {doc.document_number}</p>
          </div>

          <div className="doc-meta-grid">
            {doc.issuing_authority && (
              <div>
                <dt>Cơ quan ban hành</dt>
                <dd>{doc.issuing_authority}</dd>
              </div>
            )}
            {doc.issued_date && (
              <div>
                <dt>Ngày ban hành</dt>
                <dd>{doc.issued_date}</dd>
              </div>
            )}
            {doc.effective_date && (
              <div>
                <dt>Ngày có hiệu lực</dt>
                <dd>{doc.effective_date}</dd>
              </div>
            )}
            {doc.law_as_of && (
              <div>
                <dt>Dữ liệu kỹ thuật</dt>
                <dd>Cập nhật đến ngày {doc.law_as_of}</dd>
              </div>
            )}
          </div>

          {doc.official_url && (
            <div className="doc-header-footer">
              <a
                href={doc.official_url}
                target="_blank"
                rel="noreferrer noopener"
                className="official-link"
              >
                Văn bản gốc chính thức ↗
              </a>
            </div>
          )}
        </section>
      )}

      <section className="articles-section">
        <div className="articles-section-header">
          <h2>Danh sách điều luật</h2>

          <form onSubmit={handleSearchSubmit} className="articles-search-form">
            <label htmlFor="article-search-input" className="sr-only">
              Tìm kiếm điều luật
            </label>
            <input
              id="article-search-input"
              type="text"
              placeholder="Tìm theo số điều hoặc tiêu đề (ví dụ: 169, nghỉ hưu)..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="search-input"
            />
            <button type="submit" className="btn-search">
              Tìm điều
            </button>
            {activeQuery && (
              <button
                type="button"
                className="btn-clear"
                onClick={() => {
                  setSearchInput('')
                  setActiveQuery('')
                  setPage(1)
                }}
              >
                Xóa tìm kiếm
              </button>
            )}
          </form>
        </div>

        {isArticlesLoading && (
          <div className="state-panel loading-state" aria-live="polite">
            <span className="spinner" />
            <span>Đang tải danh sách điều luật...</span>
          </div>
        )}

        {articlesError && (
          <div className="state-panel error-state" role="alert">
            <p className="error-title">Lỗi tải danh sách điều luật</p>
            <p className="error-body">{articlesError}</p>
          </div>
        )}

        {!isArticlesLoading && !articlesError && articlesData && (
          <>
            {articlesData.articles.length === 0 ? (
              <div className="state-panel empty-state">
                <p>Không tìm thấy điều luật phù hợp với truy vấn "{activeQuery}".</p>
              </div>
            ) : (
              <div className="article-list">
                {articlesData.articles.map((art) => (
                  <article
                    className="article-card"
                    key={art.article_code}
                  >
                    <div className="article-card-header">
                      <span className="article-num">
                        {art.article_number !== null ? `Điều ${art.article_number}` : 'Điều luật'}
                      </span>
                      <span className="article-code-tag">{art.article_code}</span>
                    </div>

                    <h3 className="article-card-title">
                      {art.title || art.heading || `Điều ${art.article_number || ''}`}
                    </h3>

                    {(art.chapter_number || art.section_number) && (
                      <p className="article-hierarchy">
                        {art.chapter_number && `Chương ${art.chapter_number}${art.chapter_title ? `: ${art.chapter_title}` : ''}`}
                        {art.chapter_number && art.section_number && ' · '}
                        {art.section_number && `Mục ${art.section_number}${art.section_title ? `: ${art.section_title}` : ''}`}
                      </p>
                    )}

                    <div className="article-card-action">
                      <button
                        type="button"
                        className="action-text-btn"
                        onClick={() => setSelectedArticleCode(art.article_code)}
                        aria-label={`Xem nội dung Điều ${art.article_number || art.article_code}`}
                      >
                        Xem nội dung điều luật →
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )}

            {pagination && pagination.total_pages > 1 && (
              <nav className="pagination-nav" aria-label="Phân trang điều luật">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="btn-page"
                >
                  ← Trang trước
                </button>
                <span className="pagination-info">
                  Trang {pagination.page} / {pagination.total_pages} (Tổng {pagination.total} điều)
                </span>
                <button
                  type="button"
                  disabled={page >= pagination.total_pages}
                  onClick={() => setPage((p) => Math.min(pagination.total_pages, p + 1))}
                  className="btn-page"
                >
                  Trang sau →
                </button>
              </nav>
            )}
          </>
        )}
      </section>

      {selectedArticleCode && (
        <ArticleViewer
          articleCode={selectedArticleCode}
          onClose={() => setSelectedArticleCode(null)}
        />
      )}
    </div>
  )
}
