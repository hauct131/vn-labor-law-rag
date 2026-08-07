import { useState } from 'react'
import type { LegalSource } from '../api/qaTypes'

function sourceTypeLabel(sourceType: string | null) {
  if (sourceType === 'LQ') return 'Bộ luật/Luật'
  if (sourceType === 'NĐ') return 'Nghị định'
  if (sourceType === 'TT') return 'Thông tư'
  return sourceType
}

export function CitationCard({
  source,
  index,
}: {
  source: LegalSource
  index: number
}) {
  const [isExcerptOpen, setIsExcerptOpen] = useState(false)
  const [isTechOpen, setIsTechOpen] = useState(false)

  const badgeText = source.source_id || `S${index + 1}`

  // Article Title headline
  const title =
    source.citation_label ||
    (source.article_title
      ? (source.article_number ? `Điều ${source.article_number} — ${source.article_title}` : source.article_title)
      : source.article_code || 'Trích đoạn luật')

  // Document metadata subtitle
  const docInfoParts: string[] = []
  if (source.document_number) {
    docInfoParts.push(`Văn bản số ${source.document_number}`)
  }
  if (source.document_title && source.document_title !== source.document_number) {
    docInfoParts.push(source.document_title)
  }
  if (source.source_type) {
    const type = sourceTypeLabel(source.source_type)
    if (type) docInfoParts.push(type)
  }
  const subTitle = docInfoParts.join(' · ') || 'Văn bản quy phạm pháp luật'

  const articleUrl = source.article_code
    ? `/sources/${encodeURIComponent(source.article_code)}`
    : null

  return (
    <article className="citation-card">
      <div className="citation-card-header">
        <span className="citation-badge">{badgeText}</span>
        <div className="citation-title-group">
          <h4 className="citation-article-title">{title}</h4>
          <p className="citation-doc-info">{subTitle}</p>
        </div>
      </div>

      <div className="citation-actions">
        <button
          type="button"
          className="citation-btn citation-btn-ghost"
          onClick={() => setIsExcerptOpen((prev) => !prev)}
          aria-expanded={isExcerptOpen}
          aria-controls={`excerpt-${index}`}
        >
          {isExcerptOpen ? 'Thu gọn' : 'Xem trích đoạn'}
        </button>

        {articleUrl && (
          <a
            href={articleUrl}
            className="citation-btn citation-btn-outline"
            target="_blank"
            rel="noreferrer noopener"
          >
            Mở điều luật
          </a>
        )}

        {source.source_url && (
          <a
            href={source.source_url}
            className="citation-link-external"
            target="_blank"
            rel="noreferrer noopener"
          >
            Văn bản chính thức ↗
          </a>
        )}

        <button
          type="button"
          className="citation-tech-trigger"
          onClick={() => setIsTechOpen((prev) => !prev)}
          aria-expanded={isTechOpen}
        >
          {isTechOpen ? 'Ẩn chi tiết' : 'Chi tiết kỹ thuật'}
        </button>
      </div>

      {isExcerptOpen && (
        <div className="citation-excerpt" id={`excerpt-${index}`}>
          <p>{source.content}</p>
        </div>
      )}

      {isTechOpen && (
        <div className="citation-tech-details">
          <span>Hạng: <strong>{source.rank}</strong></span>
          <span>Score: <strong>{source.score !== null ? source.score.toFixed(4) : 'N/A'}</strong></span>
          {source.article_code && <span>Mã điều luật: <code>{source.article_code}</code></span>}
          {source.source_id && <span>Source ID: <code>{source.source_id}</code></span>}
          {source.chunk_id && <span>Chunk ID: <code>{source.chunk_id}</code></span>}
        </div>
      )}
    </article>
  )
}
