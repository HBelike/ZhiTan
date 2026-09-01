const MINIMUM_VISIBLE_PROGRESS = 4
const UPLOAD_PROGRESS_LIMIT = 32
const PROCESSING_PROGRESS_LIMIT = 92

export function resumeUploadProgress(loaded, total) {
  const normalizedTotal = Number(total)
  if (!Number.isFinite(normalizedTotal) || normalizedTotal <= 0) return MINIMUM_VISIBLE_PROGRESS
  const ratio = Math.max(0, Math.min(1, Number(loaded) / normalizedTotal))
  return Math.max(MINIMUM_VISIBLE_PROGRESS, Math.round(ratio * UPLOAD_PROGRESS_LIMIT))
}

export function nextResumeProcessingProgress(current) {
  const progress = Math.max(UPLOAD_PROGRESS_LIMIT, Number(current) || 0)
  if (progress >= PROCESSING_PROGRESS_LIMIT) return PROCESSING_PROGRESS_LIMIT
  if (progress < 64) return Math.min(PROCESSING_PROGRESS_LIMIT, progress + 4)
  if (progress < 82) return Math.min(PROCESSING_PROGRESS_LIMIT, progress + 2)
  return Math.min(PROCESSING_PROGRESS_LIMIT, progress + 1)
}
