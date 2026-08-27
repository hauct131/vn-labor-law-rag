import type { LegalSource } from '../api/qaTypes'
import { CitationCard } from './CitationCard'

export function CitationList({ sources, title = 'Căn cứ pháp luật' }: { sources: LegalSource[]; title?: string }) {
  if (!sources || sources.length === 0) return null

  return (
    <div className="citation-section">
      <div className="citation-section-header">
        <h3 className="citation-section-title">{title}</h3>
        <span className="citation-count-badge">{sources.length} nguồn</span>
      </div>

      <div className="citation-list">
        {sources.map((source, index) => (
          <CitationCard
            key={`${source.chunk_id}-${index}`}
            source={source}
            index={index}
          />
        ))}
      </div>
    </div>
  )
}
