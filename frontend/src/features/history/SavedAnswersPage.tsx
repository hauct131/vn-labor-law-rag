import { useEffect, useState } from 'react'
import { AnswerCard, type DisplayAnswer } from './AnswerCard'
import { deleteBookmark, fetchSavedAnswers } from './conversationApi'
import type { SavedAnswer } from './conversationTypes'

function toDisplayAnswer(item: SavedAnswer): DisplayAnswer {
  const answer = item.answer
  return {
    id: answer.id,
    content: answer.content,
    method: answer.retrieval_method,
    sources: answer.sources,
    retrievalMs: answer.retrieval_ms,
    generationMs: answer.generation_ms,
    totalMs: answer.total_ms,
    model: answer.model,
    insufficientEvidence: answer.insufficient_evidence,
    outOfScope: answer.out_of_scope,
    generationFailed: answer.generation_failed,
    bookmarked: true,
  }
}

export function SavedAnswersPage({
  onOpenConversation,
}: {
  onOpenConversation: (conversationId: string) => void
}) {
  const [items, setItems] = useState<SavedAnswer[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyMessageId, setBusyMessageId] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setIsLoading(true)
    fetchSavedAnswers(controller.signal)
      .then(setItems)
      .catch((requestError: unknown) => {
        if (requestError instanceof DOMException && requestError.name === 'AbortError') return
        setError(requestError instanceof Error
          ? requestError.message
          : 'Không thể tải câu trả lời đã lưu.')
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false)
      })
    return () => controller.abort()
  }, [])

  async function remove(messageId: string) {
    setBusyMessageId(messageId)
    setError('')
    try {
      await deleteBookmark(messageId)
      setItems((current) => current.filter((item) => item.answer.id !== messageId))
    } catch (requestError) {
      setError(requestError instanceof Error
        ? requestError.message
        : 'Không thể bỏ đánh dấu.')
    } finally {
      setBusyMessageId(null)
    }
  }

  return (
    <section className="saved-page">
      <header className="saved-page-header">
        <p className="eyebrow">Không gian nghiên cứu</p>
        <h1>Câu trả lời đã đánh dấu</h1>
        <p>Lưu các kết quả quan trọng cùng nguyên trạng nguồn trích dẫn.</p>
      </header>

      {isLoading && <p className="saved-state">Đang tải dữ liệu đã lưu…</p>}
      {error && <p className="saved-state history-error" role="alert">{error}</p>}
      {!isLoading && !error && items.length === 0 && (
        <div className="saved-empty">
          <span aria-hidden="true">☆</span>
          <h2>Chưa có câu trả lời được đánh dấu</h2>
          <p>Mở một hội thoại và chọn “Đánh dấu” ở câu trả lời cần lưu.</p>
        </div>
      )}

      <div className="saved-list">
        {items.map((item) => (
          <article className="saved-answer-card" key={item.bookmark.id}>
            <div className="saved-answer-context">
              <div>
                <p className="eyebrow">{item.conversation_title}</p>
                {item.question && <h2>{item.question}</h2>}
              </div>
              <button
                type="button"
                onClick={() => onOpenConversation(item.conversation_id)}
              >
                Mở hội thoại →
              </button>
            </div>
            <AnswerCard
              answer={toDisplayAnswer(item)}
              isBookmarkBusy={busyMessageId === item.answer.id}
              onToggleBookmark={(messageId) => void remove(messageId)}
            />
          </article>
        ))}
      </div>
    </section>
  )
}
