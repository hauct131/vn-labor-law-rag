import { type FormEvent, useEffect, useMemo, useState } from 'react'
import Markdown from 'react-markdown'
import './App.css'
import { API_BASE_URL } from './api/apiConfig'
import type { LegalArticleResponse } from './api/legalArticleTypes'
import './features/library/documentLibrary.css'
import { DocumentLibraryPage } from './features/library/DocumentLibraryPage'
import { DocumentDetailPage } from './features/library/DocumentDetailPage'

type RetrievalMethod = 'sparse' | 'dense' | 'hybrid'

type LegalSource = {
  source_id: string | null
  chunk_id: string
  article_code: string | null
  article_number: string | null
  article_title: string | null
  document_title: string | null
  document_number: string | null
  citation_label: string | null
  clause_number: string | null
  point_labels: string[]
  content: string
  score: number | null
  rank: number
  retrieval_origin: string | null
  source_type: string | null
  source_url: string | null
  component_ranks: Record<string, number>
}

type AskResponse = {
  answer: string
  method: RetrievalMethod
  sources: LegalSource[]
  retrieval_ms: number
  generation_ms: number
  total_ms: number
  model: string | null
  insufficient_evidence: boolean
  out_of_scope: boolean
  generation_failed: boolean
}



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

function formatMs(value: number) {
  if (value >= 1000) return `${(value / 1000).toFixed(2)} giây`
  return `${Math.round(value)} ms`
}

function sourceLocation(source: LegalSource) {
  const parts: string[] = []
  if (source.clause_number) parts.push(`Khoản ${source.clause_number}`)
  if (source.point_labels.length) {
    parts.push(`Điểm ${source.point_labels.join(', ')}`)
  }
  return parts.join(' · ')
}

function sourceTypeLabel(sourceType: string | null) {
  if (sourceType === 'LQ') return 'Bộ luật/Luật'
  if (sourceType === 'NĐ') return 'Nghị định'
  if (sourceType === 'TT') return 'Thông tư'
  return sourceType
}

async function readError(response: Response) {
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // The fallback below is clearer than exposing an invalid provider body.
  }
  return `Yêu cầu thất bại (HTTP ${response.status}).`
}

function articlePagePath(articleCode: string) {
  return `/sources/${encodeURIComponent(articleCode)}`
}

function navigateTo(path: string) {
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function TopNav({
  activeTab,
  badgeText = 'Phiên bản v1.0',
  showBackLink = false,
}: {
  activeTab: 'qa' | 'library' | 'source'
  badgeText?: string
  showBackLink?: boolean
}) {
  return (
    <header className="topbar">
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
        <span>
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
          💬 Hỏi đáp
        </button>
        <button
          type="button"
          className={`nav-tab ${activeTab === 'library' ? 'active' : ''}`}
          onClick={() => navigateTo('/library')}
        >
          📚 Thư viện pháp luật
        </button>
      </nav>

      {showBackLink ? (
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
      ) : (
        <span className="mvp-badge">{badgeText}</span>
      )}
    </header>
  )
}

function LegalArticlePage({ articleCode }: { articleCode: string }) {
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
      <TopNav activeTab="source" showBackLink />

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

function QuestionAnswerPage() {
  const [question, setQuestion] = useState(examples[0])
  const [method, setMethod] = useState<RetrievalMethod>('hybrid')
  const [result, setResult] = useState<AskResponse | null>(null)
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const activeMethod = useMemo(
    () => methods.find((item) => item.value === method),
    [method],
  )

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalizedQuestion = question.trim()
    if (normalizedQuestion.length < 3) {
      setError('Vui lòng nhập câu hỏi có ít nhất 3 ký tự.')
      return
    }

    setError('')
    setResult(null)
    setIsLoading(true)
    try {
      const response = await fetch(`${API_BASE_URL}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: normalizedQuestion, method }),
      })
      if (!response.ok) throw new Error(await readError(response))
      setResult((await response.json()) as AskResponse)
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Không thể kết nối đến hệ thống.'
      setError(message === 'Failed to fetch'
        ? 'Không kết nối được backend tại cổng 8000. Hãy kiểm tra API đang chạy.'
        : message)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="app-shell">
      <TopNav activeTab="qa" badgeText="MVP · Sparse, Dense & Hybrid" />

      <main id="top">
        <section className="intro">
          <p className="eyebrow">Tra cứu có căn cứ nguồn</p>
          <h1>Hỏi đáp pháp luật lao động<br />bằng tiếng Việt</h1>
          <p className="intro-copy">
            Hệ thống truy hồi các điều khoản liên quan trước khi tạo câu trả lời,
            đồng thời hiển thị nguồn để bạn tự đối chiếu.
          </p>
        </section>

        <section className="workspace" aria-label="Khu vực hỏi đáp">
          <form className="query-panel" onSubmit={handleSubmit}>
            <fieldset>
              <legend>1. Chọn phương pháp truy hồi</legend>
              <div className="method-grid">
                {methods.map((item) => (
                  <label className="method-card" key={item.value}>
                    <input
                      type="radio"
                      name="method"
                      value={item.value}
                      checked={method === item.value}
                      onChange={() => setMethod(item.value)}
                    />
                    <span className="radio-dot" aria-hidden="true" />
                    <span>
                      <strong>{item.name}</strong>
                      <small>{item.description}</small>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>

            <div className="question-field">
              <label htmlFor="question">2. Nhập câu hỏi</label>
              <textarea
                id="question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Ví dụ: Người lao động được nghỉ hằng năm bao nhiêu ngày?"
                maxLength={2000}
                rows={5}
              />
              <span className="character-count">{question.length}/2000</span>
            </div>

            <div className="examples" aria-label="Câu hỏi gợi ý">
              <span>Gợi ý:</span>
              {examples.map((example, index) => (
                <button
                  type="button"
                  key={example}
                  onClick={() => setQuestion(example)}
                >
                  Câu {index + 1}
                </button>
              ))}
            </div>

            <button className="submit-button" type="submit" disabled={isLoading}>
              {isLoading ? (
                <><span className="spinner" aria-hidden="true" />Đang tra cứu…</>
              ) : (
                <>Tra cứu với {activeMethod?.name}<span aria-hidden="true">→</span></>
              )}
            </button>
            <p className="quota-note">
              Mỗi câu hỏi thật sử dụng 1 lượt OpenRouter miễn phí.
            </p>
          </form>

          <section className="result-panel" aria-live="polite">
            {!result && !error && !isLoading && (
              <div className="empty-state">
                <span aria-hidden="true">¶</span>
                <h2>Câu trả lời sẽ xuất hiện tại đây</h2>
                <p>Bao gồm nội dung trả lời, điều luật nguồn và thời gian xử lý.</p>
              </div>
            )}

            {isLoading && (
              <div className="empty-state loading-state">
                <span className="large-spinner" aria-hidden="true" />
                <h2>Đang tìm căn cứ pháp lý</h2>
                <p>Lần chạy Dense/Hybrid đầu tiên có thể lâu hơn vì cần nạp mô hình E5.</p>
              </div>
            )}

            {error && (
              <div className="error-state" role="alert">
                <strong>Chưa thể trả lời</strong>
                <p>{error}</p>
              </div>
            )}

            {result && (
              <div className="answer-stack">
                <div className="answer-header">
                  <div>
                    <p className="eyebrow">Kết quả · {result.method}</p>
                    <h2>Câu trả lời</h2>
                  </div>
                  {result.out_of_scope ? (
                    <span className="warning-badge">Ngoài phạm vi</span>
                  ) : result.generation_failed ? (
                    <span className="warning-badge">Lỗi sinh đáp án</span>
                  ) : result.insufficient_evidence ? (
                    <span className="warning-badge">Thiếu căn cứ</span>
                  ) : null}
                </div>

                <div className="answer-text">
                  <Markdown
                    components={{
                      a: ({ children, ...props }) => (
                        <a {...props} target="_blank" rel="noreferrer">
                          {children}
                        </a>
                      ),
                    }}
                  >
                    {result.answer}
                  </Markdown>
                </div>

                <dl className="metrics">
                  <div><dt>Truy hồi</dt><dd>{formatMs(result.retrieval_ms)}</dd></div>
                  <div><dt>Sinh đáp án</dt><dd>{formatMs(result.generation_ms)}</dd></div>
                  <div><dt>Tổng cộng</dt><dd>{formatMs(result.total_ms)}</dd></div>
                  <div><dt>Mô hình</dt><dd title={result.model || ''}>{result.model || 'Không gọi LLM'}</dd></div>
                </dl>

                <div className="sources-heading">
                  <h3>Nguồn đối chiếu</h3>
                  <span>{result.sources.length} đoạn luật</span>
                </div>

                <div className="source-list">
                  {result.sources.map((source, index) => (
                    <article className="source-card" key={source.chunk_id}>
                      <div className="source-topline">
                        <span className="source-index">
                          {source.source_id || `S${index + 1}`}
                        </span>
                        {source.source_type && (
                          <span className="source-type">
                            {sourceTypeLabel(source.source_type)}
                          </span>
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
                        <p className="source-code">
                          Mã pháp điển: {source.article_code}
                        </p>
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
                              rel="noreferrer"
                            >
                              Xem đầy đủ điều luật
                            </a>
                          )}
                          {source.source_url && (
                            <a href={source.source_url} target="_blank" rel="noreferrer">
                              Văn bản chính thức ↗
                            </a>
                          )}
                        </span>
                      </div>
                    </article>
                  ))}
                </div>
              </div>
            )}
          </section>
        </section>
      </main>

      <footer>
        <p>
          Công cụ hỗ trợ tra cứu học thuật, không thay thế tư vấn pháp lý chuyên môn.
        </p>
      </footer>
    </div>
  )
}

type Route =
  | { type: 'qa' }
  | { type: 'source'; articleCode: string }
  | { type: 'library-list' }
  | { type: 'library-detail'; documentId: string }

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

  if (pathname === '/library' || pathname.startsWith('/library/')) {
    return { type: 'library-list' }
  }

  return { type: 'qa' }
}

function App() {
  const [currentPath, setCurrentPath] = useState(window.location.pathname)

  useEffect(() => {
    const handlePopState = () => setCurrentPath(window.location.pathname)
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  const route = parseRoute(currentPath)

  if (route.type === 'source') {
    return <LegalArticlePage articleCode={route.articleCode} />
  }

  if (route.type === 'library-detail') {
    return (
      <div className="app-shell">
        <TopNav activeTab="library" />
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
  }

  if (route.type === 'library-list') {
    return (
      <div className="app-shell">
        <TopNav activeTab="library" />
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
  }

  return <QuestionAnswerPage />
}

export default App
