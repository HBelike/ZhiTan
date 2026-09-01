export const ONLINE_ASSESSMENT_PATH = '/career/online-assessment'
export const ONLINE_ASSESSMENT_WINDOW_NAME = 'career-online-assessment'

export function openOnlineAssessmentAssistant({
  origin = window.location.origin,
  openWindow = window.open.bind(window)
} = {}) {
  const opened = openWindow(
    new URL(ONLINE_ASSESSMENT_PATH, origin).toString(),
    ONLINE_ASSESSMENT_WINDOW_NAME,
    'popup=yes,width=1460,height=940,resizable=yes,scrollbars=yes'
  )
  opened?.focus?.()
  return Boolean(opened)
}
