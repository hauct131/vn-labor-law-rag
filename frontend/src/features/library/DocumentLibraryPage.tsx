import { type FormEvent, useEffect, useState } from 'react'
import { fetchDocuments } from './documentLibraryApi'
import type {
  DocumentListResponse,
  DocumentSummary,
} from './documentLibraryTypes'

interface DocumentLibraryPageProps {
  onSelectDocument: (documentId: string) => void
}

function sourceTypeLabel(sourceType: string | null) {
  if (sourceType === 'LQ') return 'Bộ luật/Luật'
  if (sourceType === 'NĐ') return 'Nghị định'
  if (sourceType === 'TT') return 'Thông tư'
  return sourceType || 'Văn bản'
}

export function DocumentLibraryPage({
  onSelectDocument,
}: DocumentLibraryPageProps) {
  const [data, setData] = useState<DocumentListResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')

  const [searchInput, setSearchInput] = useState('')
  const [activeQuery, setActiveQuery] = useState('')
  const [selectedType, setSelectedType] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 12

  useEffect(() => {
    const controller = new AbortController()

    async function loadDocuments() {
      setIsLoading(true)
      setError('')
      try {
        const res = await fetchDocuments(
          {
            q: activeQuery,
            document_type: selectedType,
            page,
            page_size: pageSize,
          },
          controller.signal,
        )
        setData(res)
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setError(
          err instanceof Error
            ? err.message
            : 'Không thể tải danh sách văn bản pháp luật.',
        )
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false)
        }
      }
    }

    void loadDocuments()
    return () => controller.abort()
  }, [activeQuery, selectedType, page])

  const handleSearchSubmit = (e: FormEvent) => {
    e.preventDefault()
    setActiveQuery(searchInput)
    setPage(1)
  }

  const handleTypeChange = (newType: string) => {
    setSelectedType(newType)
    setPage(1)
  }

  const pagination = data?.pagination

  return (
    <div className="document-library-container">
      <div className="library-intro">
        <p className="eyebrow">Thư viện tra cứu</p>
        <h1 className="library-title">Thư viện văn bản pháp luật</h1>
        <p className="library-subtitle">
          Tra cứu toàn văn các Bộ luật, Luật, Nghị định và Thông tư trong lĩnh vực lao động Việt Nam.
        </p>
        {data?.law_as_of && (
          <p className="technical-date-badge">
            Dữ liệu kỹ thuật cập nhật đến ngày {data.law_as_of}
          </p>
        )}
      </div>

      <div className="library-filter-bar">
        <form onSubmit={handleSearchSubmit} className="library-search-form">
          <div className="search-field">
            <label htmlFor="doc-search-input">Tìm kiếm văn bản</label>
            <input
              id="doc-search-input"
              type="text"
              placeholder="Nhập số hiệu hoặc tên văn bản (ví dụ: 45/2019, Bộ luật Lao động)..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="search-input"
            />
          </div>

          <div className="type-filter-field">
            <label htmlFor="doc-type-select">Loại văn bản</label>
            <select
              id="doc-type-select"
              value={selectedType}
              onChange={(e) => handleTypeChange(e.target.value)}
              className="filter-select"
            >
              <option value="">Tất cả loại văn bản</option>
              <option value="LQ">Bộ luật / Luật (LQ)</option>
              <option value="NĐ">Nghị định (NĐ)</option>
              <option value="TT">Thông tư (TT)</option>
            </select>
          </div>

          <div className="form-actions">
            <button type="submit" className="btn-primary">
              Tìm kiếm
            </button>
            {(activeQuery || selectedType) && (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setSearchInput('')
                  setActiveQuery('')
                  setSelectedType('')
                  setPage(1)
                }}
              >
                Xóa bộ lọc
              </button>
            )}
          </div>
        </form>
      </div>

      {isLoading && (
        <div className="state-panel loading-state" aria-live="polite">
          <span className="spinner" />
          <span>Đang tải danh sách văn bản pháp luật...</span>
        </div>
      )}

      {error && (
        <div className="state-panel error-state" role="alert">
          <p className="error-title">Lỗi tải dữ liệu</p>
          <p className="error-body">{error}</p>
        </div>
      )}

      {!isLoading && !error && data && (
        <>
          <div className="results-meta">
            <span>
              Hiển thị {data.documents.length} / Tổng số {pagination?.total || 0} văn bản
            </span>
          </div>

          {data.documents.length === 0 ? (
            <div className="state-panel empty-state">
              <p>Không tìm thấy văn bản phù hợp với điều kiện tìm kiếm.</p>
            </div>
          ) : (
            <div className="document-grid">
              {data.documents.map((doc: DocumentSummary) => (
                <article
                  className="document-card"
                  key={doc.document_id}
                >
                  <div className="doc-card-header">
                    <span className="doc-type-tag">
                      {sourceTypeLabel(doc.source_type)}
                    </span>
                    <span className="doc-article-count">
                      {doc.article_count} điều
                    </span>
                  </div>

                  <h2 className="doc-card-title">
                    {doc.title || doc.document_number}
                  </h2>

                  <p className="doc-card-number">Số hiệu: {doc.document_number}</p>

                  <div className="doc-card-details">
                    {doc.issuing_authority && (
                      <p>
                        <strong>Ban hành:</strong> {doc.issuing_authority}
                      </p>
                    )}
                    {doc.issued_date && (
                      <p>
                        <strong>Ngày ban hành:</strong> {doc.issued_date}
                      </p>
                    )}
                  </div>

                  <div className="doc-card-actions">
                    <button
                      type="button"
                      className="btn-view-doc"
                      onClick={() => onSelectDocument(doc.document_id)}
                      aria-label={`Xem các điều luật thuộc ${doc.title || doc.document_number}`}
                    >
                      Xem các điều luật →
                    </button>
                    {doc.official_url && (
                      <a
                        href={doc.official_url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="official-link-small"
                      >
                        Nguồn gốc ↗
                      </a>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}

          {pagination && pagination.total_pages > 1 && (
            <nav className="pagination-nav" aria-label="Phân trang văn bản">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className="btn-page"
              >
                ← Trang trước
              </button>
              <span className="pagination-info">
                Trang {pagination.page} / {pagination.total_pages}
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
    </div>
  )
}
