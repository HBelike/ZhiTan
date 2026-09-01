export const DEFAULT_HISTORY_PAGE_SIZE = 7
export const HISTORY_PAGE_SIZE_OPTIONS = Object.freeze([5, 7, 10, 15])

export function historyPageSizeForViewportHeight(viewportHeight) {
  const normalizedHeight = Number(viewportHeight)
  if (!Number.isFinite(normalizedHeight) || normalizedHeight <= 0) return DEFAULT_HISTORY_PAGE_SIZE
  if (normalizedHeight <= 950) return 5
  if (normalizedHeight <= 1240) return 7
  return 10
}

export function resolveHistoryPageSize(value, viewportHeight) {
  const preferredSize = Number(value)
  if (HISTORY_PAGE_SIZE_OPTIONS.includes(preferredSize)) return preferredSize
  return historyPageSizeForViewportHeight(viewportHeight)
}

export function pageRequestUrl(page, pageSize) {
  const normalizedPage = Math.max(1, Number(page) || 1)
  const normalizedPageSize = Math.max(1, Number(pageSize) || DEFAULT_HISTORY_PAGE_SIZE)
  return `/api/career/conversations?page=${normalizedPage}&page_size=${normalizedPageSize}`
}

export function normalizeHistoryPage(payload, fallbackPage = 1, fallbackPageSize = DEFAULT_HISTORY_PAGE_SIZE) {
  const total = Math.max(0, Number(payload?.total) || 0)
  const pageSize = Math.max(1, Number(payload?.page_size) || fallbackPageSize)
  const totalPages = Math.max(1, Number(payload?.total_pages) || Math.ceil(total / pageSize) || 1)
  const requestedPage = Math.max(1, Number(payload?.page) || fallbackPage)
  return {
    items: Array.isArray(payload?.items) ? payload.items : [],
    page: Math.min(requestedPage, totalPages),
    pageSize,
    total,
    totalPages
  }
}

export function historyPageRange({ page, pageSize, total }) {
  if (!total) return { start: 0, end: 0 }
  const start = (page - 1) * pageSize + 1
  return { start, end: Math.min(total, start + pageSize - 1) }
}

export function normalizeHistoryPageTarget(page, totalPages) {
  const normalizedTotalPages = Math.max(1, Number(totalPages) || 1)
  const requestedPage = Math.trunc(Number(page) || 1)
  return Math.min(normalizedTotalPages, Math.max(1, requestedPage))
}
