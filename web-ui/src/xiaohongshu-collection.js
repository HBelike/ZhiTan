export const XIAOHONGSHU_COLLECTION_CAPABILITY = 'xiaohongshu_keyword_collection'
export const XIAOHONGSHU_COLLECTION_JOB_STORAGE_KEY = 'find-job-xiaohongshu-collection-job-id'

const TERMINAL_CANDIDATE_STATUSES = new Set(['fetched', 'empty', 'blocked', 'selected', 'imported', 'failed'])
const ACCOUNT_BLOCKING_CODES = new Set(['login_required', 'verification_required', 'rate_limited'])

function plainText(value, maximum = 4000) {
  return typeof value === 'string' ? value.trim().slice(0, maximum) : ''
}

export function normalizeXiaohongshuCollectionLimit(value) {
  return Math.max(5, Math.min(50, Number(value) || 20))
}

export function hasXiaohongshuCollectionCapability(connection) {
  return Array.isArray(connection?.capabilities)
    && connection.capabilities.includes(XIAOHONGSHU_COLLECTION_CAPABILITY)
}

export function createXiaohongshuDiscoveryPayload(cards) {
  return {
    items: (Array.isArray(cards) ? cards : []).map((card) => ({
      note_id: plainText(card?.noteId, 24),
      title: plainText(card?.title, 300) || null,
      author_name: plainText(card?.authorName, 120) || null,
      liked_count: plainText(card?.likedCount, 40) || null
    })).filter((item) => item.note_id.length === 24)
  }
}

export function createXiaohongshuNotePayload(note) {
  return {
    note_id: plainText(note?.noteId, 24),
    title: plainText(note?.title, 300) || null,
    body_text: plainText(note?.bodyText, 100000),
    author_name: plainText(note?.authorName, 120) || null,
    published_at: plainText(note?.publishedAt, 80) || null,
    tags: (Array.isArray(note?.tags) ? note.tags : []).map((item) => plainText(item, 60)).filter(Boolean).slice(0, 30),
    image_urls: (Array.isArray(note?.imageUrls) ? note.imageUrls : []).map((item) => plainText(item, 5000)).filter(Boolean).slice(0, 20),
    source_error_code: plainText(note?.sourceErrorCode, 80) || null,
    source_error_message: plainText(note?.sourceErrorMessage, 1000) || null
  }
}

export function candidateNoteId(candidate) {
  return plainText(candidate?.metadata?.note_id ?? candidate?.metadata_json?.note_id, 24)
}

export function isXiaohongshuCandidateTerminal(candidate) {
  return TERMINAL_CANDIDATE_STATUSES.has(String(candidate?.status ?? '').toLowerCase())
}

export function selectXiaohongshuCardsToProcess(cards, candidates) {
  const byNoteId = new Map(
    (Array.isArray(candidates) ? candidates : [])
      .map((candidate) => [candidateNoteId(candidate), candidate])
      .filter(([noteId]) => noteId)
  )
  return (Array.isArray(cards) ? cards : []).filter((card) => {
    const candidate = byNoteId.get(plainText(card?.noteId, 24))
    return candidate && !isXiaohongshuCandidateTerminal(candidate)
  })
}

export function isXiaohongshuAccountBlockingError(error) {
  return error?.stopBatch === true
    || ACCOUNT_BLOCKING_CODES.has(String(error?.code ?? '').toLowerCase())
}
