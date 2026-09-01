export const INTERVIEW_MASTER_PATH = '/career/interview-master'
export const QWEN_ASR_PRICE_PER_SECOND = 0.00033

export function isInterviewMasterPath(pathname) {
  return pathname === INTERVIEW_MASTER_PATH
}

export function formatDuration(totalSeconds) {
  const value = Math.max(0, Math.floor(Number(totalSeconds) || 0))
  const hours = Math.floor(value / 3600)
  const minutes = Math.floor((value % 3600) / 60)
  const seconds = value % 60
  return [hours ? String(hours).padStart(2, '0') : null, String(minutes).padStart(2, '0'), String(seconds).padStart(2, '0')]
    .filter(Boolean)
    .join(':')
}

export function estimateAsrCost(totalSeconds, trackCount = 1) {
  const seconds = Math.max(0, Number(totalSeconds) || 0)
  const tracks = Math.max(1, Math.min(2, Number(trackCount) || 1))
  return Number((seconds * tracks * QWEN_ASR_PRICE_PER_SECOND).toFixed(4))
}

export function pickInitialAnswerModel(models, preferredId = '') {
  const ready = (models || []).filter((item) => item.readiness === 'ready')
  return ready.some((item) => item.id === preferredId) ? preferredId : ready[0]?.id || ''
}

export function advanceSetupProgress(current, cap = 92) {
  const value = Math.max(0, Number(current) || 0)
  const limit = Math.max(value, Number(cap) || 92)
  const remaining = limit - value
  return Math.min(limit, value + Math.max(1, Math.ceil(remaining * 0.08)))
}

export function isNearScrollEnd({ scrollHeight = 0, scrollTop = 0, clientHeight = 0 } = {}, threshold = 48) {
  return Number(scrollHeight) - Number(scrollTop) - Number(clientHeight) <= Math.max(0, Number(threshold) || 0)
}

export function defaultInterviewDate(startedAt = '', fallback = new Date()) {
  const value = startedAt ? new Date(startedAt) : fallback
  const valid = Number.isNaN(value.getTime()) ? fallback : value
  const year = valid.getFullYear()
  const month = String(valid.getMonth() + 1).padStart(2, '0')
  const day = String(valid.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function buildArchivePreview(payload = {}, fallbackQuestions = []) {
  const serverQuestions = Array.isArray(payload.question_preview) ? payload.question_preview : []
  const questions = (serverQuestions.length ? serverQuestions : fallbackQuestions)
    .map((item) => typeof item === 'string' ? item : item?.question)
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .slice(0, 5)
  const requestedCount = Number(payload.question_count)
  const questionCount = Number.isFinite(requestedCount) && requestedCount >= 0
    ? requestedCount
    : questions.length
  return {
    questionCount,
    questions,
    remainingCount: Math.max(0, questionCount - questions.length),
    interviewDate: defaultInterviewDate(payload.started_at)
  }
}

export function answerPreviewLines(answerText, limit = 5) {
  return String(answerText || '')
    .split(/\r?\n/)
    .map((line) => line.replace(/^#{1,6}\s*/, '').trim())
    .filter(Boolean)
    .slice(0, limit)
}
