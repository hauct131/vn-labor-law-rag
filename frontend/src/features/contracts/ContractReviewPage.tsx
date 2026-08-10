import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { CitationList } from '../../components/CitationList'
import {
  createContractReview,
  deleteContractReview,
  fetchContractReview,
  fetchContractReviews,
} from './contractReviewApi'
import type { ContractReview, ContractReviewListItem } from './contractReviewTypes'
import './contractReview.css'

function formatBytes(value: number) {
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MB`
  return `${Math.max(1, Math.round(value / 1024))} KB`
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('vi-VN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

const severityLabels = {
  info: 'Thông tin',
  attention: 'Cần kiểm tra',
  warning: 'Ưu tiên kiểm tra',
  insufficient_evidence: 'Không đủ căn cứ',
}

export function ContractReviewPage({ isAuthenticated, onLogin }: {
  isAuthenticated: boolean
  onLogin: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [review, setReview] = useState<ContractReview | null>(null)
  const [items, setItems] = useState<ContractReviewListItem[]>([])
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isLoadingList, setIsLoadingList] = useState(false)
  const [error, setError] = useState('')

  const reloadList = useCallback(async (signal?: AbortSignal) => {
    if (!isAuthenticated) return
    if (signal?.aborted) return
    setIsLoadingList(true)
    try {
      setItems(await fetchContractReviews(signal))
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === 'AbortError') return
      setError(requestError instanceof Error ? requestError.message : 'Không thể tải lịch sử rà soát.')
    } finally {
      if (!signal?.aborted) setIsLoadingList(false)
    }
  }, [isAuthenticated])

  useEffect(() => {
    const controller = new AbortController()
    queueMicrotask(() => {
      if (!controller.signal.aborted) void reloadList(controller.signal)
    })
    return () => controller.abort()
  }, [reloadList])

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!file || isSubmitting) return
    setIsSubmitting(true)
    setError('')
    try {
      const result = await createContractReview(file)
      setReview(result)
      await reloadList()
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Không thể rà soát hợp đồng.')
    } finally {
      setIsSubmitting(false)
    }
  }

  async function openReview(reviewId: string) {
    setError('')
    try {
      setReview(await fetchContractReview(reviewId))
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Không thể mở báo cáo.')
    }
  }

  async function removeReview(reviewId: string) {
    if (!window.confirm('Xóa báo cáo rà soát này?')) return
    try {
      await deleteContractReview(reviewId)
      if (review?.id === reviewId) setReview(null)
      await reloadList()
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Không thể xóa báo cáo.')
    }
  }

  if (!isAuthenticated) {
    return (
      <section className="contract-auth-required">
        <p className="eyebrow">Dữ liệu hợp đồng riêng tư</p>
        <h1>Cần đăng nhập để rà soát hợp đồng</h1>
        <p>Hợp đồng và báo cáo được tách biệt theo tài khoản. File gốc không được lưu lâu dài.</p>
        <button type="button" className="auth-button" onClick={onLogin}>Đăng nhập</button>
      </section>
    )
  }

  return (
    <main className="contract-review-page">
      <section className="contract-review-hero">
        <div>
          <p className="eyebrow">Rà soát có căn cứ</p>
          <h1>Rà soát hợp đồng lao động</h1>
          <p>Kiểm tra thử việc, tiền lương, thời giờ làm việc và điều khoản chấm dứt dựa trên corpus pháp luật hiện tại.</p>
        </div>
        <form className="contract-upload-card" onSubmit={handleSubmit}>
          <label htmlFor="contract-file">Tệp hợp đồng PDF hoặc DOCX, tối đa 10 MB</label>
          <input
            id="contract-file"
            type="file"
            accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            disabled={isSubmitting}
            onChange={(event) => {
              const selected = event.target.files?.[0] || null
              setError('')
              if (!selected) {
                setFile(null)
                return
              }
              const extension = selected.name.toLowerCase().split('.').pop()
              if (extension !== 'pdf' && extension !== 'docx') {
                setFile(null)
                setError('Chỉ hỗ trợ tệp PDF hoặc DOCX.')
                event.currentTarget.value = ''
                return
              }
              if (selected.size > 10 * 1024 * 1024) {
                setFile(null)
                setError('Tệp vượt quá giới hạn 10 MB.')
                event.currentTarget.value = ''
                return
              }
              setFile(selected)
            }}
          />
          {file && <p className="contract-file-meta"><strong>{file.name}</strong> · {formatBytes(file.size)}</p>}
          <p className="contract-file-privacy">File gốc chỉ được xử lý trong request và không được lưu vào hệ thống.</p>
          <div className="contract-upload-actions">
            <span className="contract-retrieval-label">Căn cứ canonical · truy hồi từ khóa</span>
            <button type="submit" disabled={!file || isSubmitting}>
              {isSubmitting ? 'Đang trích xuất và rà soát…' : 'Rà soát hợp đồng'}
            </button>
          </div>
        </form>
      </section>

      {error && <p className="contract-error" role="alert">{error}</p>}

      <div className="contract-review-layout">
        <aside className="contract-review-history">
          <div className="contract-history-heading">
            <h2>Báo cáo trước đây</h2>
            {isLoadingList && <span>Đang tải…</span>}
          </div>
          {items.length === 0 && !isLoadingList && <p>Chưa có báo cáo nào.</p>}
          {items.map((item) => (
            <article key={item.id} className={review?.id === item.id ? 'active' : ''}>
              <button type="button" onClick={() => void openReview(item.id)}>
                <strong>{item.original_filename}</strong>
                <span>{formatDate(item.created_at)}</span>
                <small>
                  {item.finding_count} nhóm · {item.attention_count} cần kiểm tra ·{' '}
                  {item.warning_count} ưu tiên
                </small>
              </button>
              <button type="button" className="contract-delete" onClick={() => void removeReview(item.id)} aria-label={`Xóa ${item.original_filename}`}>×</button>
            </article>
          ))}
        </aside>

        <section className="contract-report-panel">
          {!review ? (
            <div className="contract-empty-report">
              <h2>Chưa có báo cáo đang mở</h2>
              <p>Chọn một hợp đồng để tạo báo cáo hoặc mở lại báo cáo đã lưu.</p>
            </div>
          ) : (
            <>
              <header className="contract-report-header">
                <div>
                  <p className="eyebrow">Báo cáo hoàn tất</p>
                  <h2>{review.original_filename}</h2>
                  <p>{formatBytes(review.file_size_bytes)} · {formatDate(review.created_at)} · {review.extracted_character_count.toLocaleString('vi-VN')} ký tự</p>
                </div>
                <span className="contract-method">Căn cứ canonical</span>
              </header>
              <p className="contract-summary">{review.summary}</p>
              <div className="contract-findings">
                {review.findings.map((finding) => (
                  <article key={finding.id} className={`contract-finding severity-${finding.severity}`}>
                    <header>
                      <div>
                        <h3>{finding.title}</h3>
                      </div>
                      <span>{severityLabels[finding.severity]}</span>
                    </header>
                    <details>
                      <summary>Xem đoạn hợp đồng</summary>
                      <blockquote>{finding.contract_excerpt}</blockquote>
                    </details>
                    <div className="contract-analysis">
                      <h4>Phân tích</h4>
                      <p>{finding.analysis}</p>
                      <h4>Khuyến nghị</h4>
                      <p>{finding.recommendation}</p>
                    </div>
                    <details className="contract-evidence">
                      <summary>Xem căn cứ pháp luật ({finding.sources.length})</summary>
                      <CitationList sources={finding.sources} />
                    </details>
                  </article>
                ))}
              </div>
              <p className="contract-disclaimer">Kết quả chỉ mang tính hỗ trợ rà soát thông tin và không thay thế tư vấn của luật sư hoặc cơ quan có thẩm quyền.</p>
            </>
          )}
        </section>
      </div>
    </main>
  )
}
