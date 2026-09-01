const WEB_CHANNEL = 'find-job-job-library-web-v1'
const EXTENSION_CHANNEL = 'find-job-job-library-extension-v1'
let requestSequence = 0

export class AssessmentBridgeError extends Error {
  constructor(code, message) {
    super(message)
    this.name = 'AssessmentBridgeError'
    this.code = code
  }
}

function requestExtension(action, payload = {}, timeoutMs = 12000) {
  return new Promise((resolve, reject) => {
    const requestId = `online-assessment-${Date.now()}-${++requestSequence}`
    const timeoutId = window.setTimeout(() => {
      window.removeEventListener('message', handleMessage)
      reject(new AssessmentBridgeError('extension_unavailable', '未检测到求职浏览器助手，请安装或更新扩展后刷新页面。'))
    }, timeoutMs)

    function handleMessage(event) {
      if (event.source !== window || event.origin !== window.location.origin) return
      const message = event.data
      if (!message || message.channel !== EXTENSION_CHANNEL || message.requestId !== requestId) return
      window.clearTimeout(timeoutId)
      window.removeEventListener('message', handleMessage)
      if (message.ok) {
        resolve(message.data)
        return
      }
      reject(new AssessmentBridgeError(
        message.error?.code ?? 'assessment_extension_error',
        message.error?.message ?? '浏览器助手没有返回题面。'
      ))
    }

    window.addEventListener('message', handleMessage)
    window.postMessage({ channel: WEB_CHANNEL, requestId, action, payload }, window.location.origin)
  })
}

export function captureToApiPayload(capture = {}) {
  return {
    source_url: String(capture.sourceUrl ?? '').slice(0, 4000),
    source_title: String(capture.sourceTitle ?? '').slice(0, 500),
    source_platform: String(capture.sourcePlatform ?? 'generic'),
    visible_text: String(capture.visibleText ?? '').slice(0, 30000),
    problem_candidates: Array.isArray(capture.problemCandidates) ? capture.problemCandidates.slice(0, 12) : [],
    starter_code: String(capture.starterCode ?? '').slice(0, 80000),
    language_hint: String(capture.languageHint ?? '').slice(0, 80),
    function_signature: String(capture.functionSignature ?? '').slice(0, 2000),
    public_test_candidates: Array.isArray(capture.publicTestCandidates)
      ? capture.publicTestCandidates.slice(0, 20)
      : [],
    screenshot_data_url: capture.screenshotDataUrl || null,
    viewport: {
      width: Number(capture.viewport?.width) || 0,
      height: Number(capture.viewport?.height) || 0,
      device_pixel_ratio: Number(capture.viewport?.devicePixelRatio) || 1
    },
    capture_mode: String(capture.captureMode ?? 'generic')
  }
}

export function mergeAssessmentCaptures(previous = {}, incoming = {}) {
  const unique = (items) => [...new Set(items.map((item) => String(item ?? '').trim()).filter(Boolean))]
  const visibleParagraphs = unique([
    ...String(previous.visibleText ?? '').split('\n'),
    ...String(incoming.visibleText ?? '').split('\n')
  ])
  return {
    ...previous,
    ...incoming,
    visibleText: visibleParagraphs.join('\n').slice(0, 30000),
    problemCandidates: unique([
      ...(previous.problemCandidates ?? []),
      ...(incoming.problemCandidates ?? [])
    ]).slice(0, 12),
    publicTestCandidates: [...(previous.publicTestCandidates ?? []), ...(incoming.publicTestCandidates ?? [])].slice(0, 20),
    starterCode: incoming.starterCode || previous.starterCode || '',
    functionSignature: incoming.functionSignature || previous.functionSignature || '',
    screenshotDataUrl: incoming.screenshotDataUrl || previous.screenshotDataUrl || ''
  }
}

export const assessmentBridge = {
  ping() {
    return requestExtension('ping', {}, 2200)
  },
  claim(launchId) {
    return requestExtension('claim_assessment_launch', { launchId })
  },
  refresh(captureToken) {
    return requestExtension('refresh_assessment_problem', { captureToken })
  },
  append(captureToken) {
    return requestExtension('append_assessment_problem', { captureToken })
  }
}
