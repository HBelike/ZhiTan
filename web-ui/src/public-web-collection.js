export const PUBLIC_WEB_COLLECTION_STAGES = [
  '搜索公开网页',
  '地址去重',
  '提取正文与 OCR',
  'Agent 甄别',
  '自动写入面经库'
]

export function normalizePublicWebCollectionLimit(value) {
  if (value === '' || value === null || value === undefined) return 10
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 10
  return Math.max(5, Math.min(10, Math.round(parsed)))
}

function count(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0
}

export function normalizePublicWebSummary(value = {}) {
  return {
    discovered: count(value.discovered_count),
    knownUrl: count(value.known_url_count),
    scraped: count(value.scraped_count),
    duplicate: count(value.duplicate_count),
    valid: count(value.valid_count),
    imported: count(value.imported_count),
    filtered: count(value.filtered_count),
    failed: count(value.failed_count)
  }
}

export function publicWebProgress(metadata = {}, status = '') {
  const phase = String(metadata.phase ?? '').toLowerCase()
  const statusValue = String(status ?? '').toLowerCase()
  const terminal = ['succeeded', 'completed', 'failed', 'cancelled'].includes(statusValue)
  let currentIndex = 0
  if (phase === 'ledger') currentIndex = 1
  if (['scrape', 'ocr'].includes(phase)) currentIndex = 2
  if (['analyze', 'filter'].includes(phase)) currentIndex = 3
  if (['import', 'completed', 'failed'].includes(phase) || terminal) currentIndex = 4
  const declared = Number(metadata.progress_percent)
  return {
    currentIndex,
    percent: Number.isFinite(declared)
      ? Math.max(0, Math.min(100, declared))
      : terminal ? 100 : currentIndex * 20,
    detail: String(metadata.progress_message ?? '')
  }
}

export function publicWebAvailability(platforms = []) {
  const platform = platforms.find((item) => item?.key === 'public_web')
  if (!platform) return { ready: false, reason: '全网公开信息收集能力尚未加载。' }
  return {
    ready: platform.ready !== false,
    reason: platform.unavailable_reason || ''
  }
}
