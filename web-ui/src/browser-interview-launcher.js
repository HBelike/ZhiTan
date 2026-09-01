import { INTERVIEW_MASTER_PATH } from './browser-live-interview/view.js'

export const INTERVIEW_MASTER_WINDOW_NAME = 'career-interview-master'

function defaultFallbackOpen(url) {
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.target = '_blank'
  anchor.rel = 'noopener'
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

export function openBrowserInterviewMaster({
  answerModelProfileId = '',
  origin = window.location.origin,
  openWindow = window.open.bind(window),
  fallbackOpen = defaultFallbackOpen
} = {}) {
  const url = new URL(INTERVIEW_MASTER_PATH, origin)
  if (answerModelProfileId) url.searchParams.set('answer_model_profile_id', answerModelProfileId)
  const opened = openWindow(
    url.toString(),
    INTERVIEW_MASTER_WINDOW_NAME,
    'popup=yes,width=1460,height=940,resizable=yes,scrollbars=yes'
  )
  if (!opened) {
    fallbackOpen(url.toString())
    return false
  }
  opened.focus?.()
  return true
}
