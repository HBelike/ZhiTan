function text(value, maximum = 800) {
  return typeof value === 'string' ? value.trim().slice(0, maximum) : ''
}

export function normalizeGreetingPayload(payload = {}) {
  const normalized = {
    securityId: text(payload.securityId, 600),
    jobId: text(payload.jobId, 300),
    bossId: text(payload.bossId, 300),
    lid: text(payload.lid, 300),
    message: text(payload.message, 2000)
  }
  if (!normalized.securityId) throw new Error('岗位安全标识不能为空。')
  if (!normalized.jobId) throw new Error('岗位标识不能为空。')
  if (!normalized.bossId) throw new Error('招聘者标识不能为空。')
  if (!normalized.lid) throw new Error('岗位来源标识不能为空。')
  if (!normalized.message) throw new Error('招呼语不能为空。')
  return normalized
}

export function buildBossChatUrl(job = {}) {
  const normalized = normalizeGreetingPayload({
    ...job,
    message: job.message || 'preflight'
  })
  const url = new URL('https://www.zhipin.com/web/geek/chat')
  url.search = new URLSearchParams({
    id: normalized.bossId,
    jobId: normalized.jobId,
    securityId: normalized.securityId,
    lid: normalized.lid
  }).toString()
  return url.toString()
}

function responseMessage(response) {
  return text(
    response?.zpData?.bizData?.chatRemindDialog?.content
      || response?.zpData?.bizData?.chatRemindDialog?.title
      || response?.message
      || response?.msg,
    500
  )
}

export const CHAT_DELIVERY_EVIDENCE = Object.freeze({
  stableMs: 2000,
  renderGapMs: 300,
  successClassPattern: '(?:^|\\s)(?:status-delivery|status-read|status-success|status-sent)(?:\\s|$)',
  failureClassPattern: '(?:^|\\s)(?:status-fail|status-failed|status-error|send-fail)(?:\\s|$)',
  successTextPattern: '已送达|已读|发送成功',
  failureTextPattern: '发送失败|发送异常|重新发送|点击重试'
})

export function normalizeOutgoingMessageText(value) {
  return String(value ?? '')
    .replace(/[\u200B-\u200D\u2060\uFEFF]/g, '')
    .replace(/\u00A0/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 4000)
}

export function hasNewLogicalOutgoingMessage(baselineTexts = [], currentTexts = [], expectedText = '') {
  const expected = normalizeOutgoingMessageText(expectedText)
  if (!expected) return false
  const baseline = (Array.isArray(baselineTexts) ? baselineTexts : []).map(normalizeOutgoingMessageText)
  const current = (Array.isArray(currentTexts) ? currentTexts : []).map(normalizeOutgoingMessageText)
  if (!current.length || current.at(-1) !== expected) return false
  if (baseline.length === current.length
    && baseline.every((value, index) => value === current[index])) {
    return false
  }

  const baselineMatches = baseline.filter((value) => value === expected).length
  const currentMatches = current.filter((value) => value === expected).length
  if (currentMatches > baselineMatches) return true

  const maximumOverlap = Math.min(baseline.length, current.length - 1)
  for (let overlap = maximumOverlap; overlap > 0; overlap -= 1) {
    const baselineSuffix = baseline.slice(-overlap)
    const currentPrefix = current.slice(0, overlap)
    if (baselineSuffix.every((value, index) => value === currentPrefix[index])) return true
  }
  return false
}

export function updateLogicalMessageObservation(
  observation = {},
  sample = {},
  now = Date.now(),
  rules = CHAT_DELIVERY_EVIDENCE
) {
  const currentTime = Number.isFinite(Number(now)) ? Number(now) : Date.now()
  const inputCleared = sample.inputCleared === true
  const messageVisible = sample.isNewLogicalMessage === true
  const previousFirstSeenAt = Number(observation.firstSeenAt) || 0
  const previousLastSeenAt = Number(observation.lastSeenAt) || 0
  const renderGapMs = Math.max(0, Number(rules.renderGapMs) || 0)

  if (!inputCleared) {
    return { firstSeenAt: 0, lastSeenAt: 0, stableForMs: 0, isNew: false, inputCleared: false }
  }
  if (messageVisible) {
    const stayedWithinRenderGap = previousFirstSeenAt > 0
      && previousLastSeenAt > 0
      && currentTime - previousLastSeenAt <= renderGapMs
    const firstSeenAt = stayedWithinRenderGap ? previousFirstSeenAt : currentTime
    return {
      firstSeenAt,
      lastSeenAt: currentTime,
      stableForMs: Math.max(0, currentTime - firstSeenAt),
      isNew: true,
      inputCleared: true
    }
  }
  if (previousFirstSeenAt > 0
    && previousLastSeenAt > 0
    && currentTime - previousLastSeenAt <= renderGapMs) {
    return {
      firstSeenAt: previousFirstSeenAt,
      lastSeenAt: previousLastSeenAt,
      stableForMs: Math.max(0, currentTime - previousFirstSeenAt),
      isNew: false,
      inputCleared: true
    }
  }
  return { firstSeenAt: 0, lastSeenAt: 0, stableForMs: 0, isNew: false, inputCleared: true }
}

export const BOSS_CHAT_PAGE_EVIDENCE = Object.freeze({
  verificationRoutePattern: '(?:verify|security-check|captcha)',
  verificationTextPattern: '请(?:先|完成).{0,12}(?:安全验证|验证码)|(?:安全验证|验证码).{0,12}(?:后继续|完成后)|拖动滑块|环境存在异常',
  verificationSelector: '.geetest_panel,.geetest_holder,.captcha,[class*="captcha"],[class*="security-check"],[data-testid*="captcha"],iframe[src*="captcha"],iframe[src*="verify"]',
  loginRoutePattern: '/web/user/?(?:[?#]|$)',
  loginTextPattern: '登录后继续|扫码登录|密码登录',
  rateLimitTextPattern: '操作过于频繁|沟通上限|沟通额度|已与\\d+位BOSS沟通'
})

export function classifyBossChatPageState(evidence = {}, rules = BOSS_CHAT_PAGE_EVIDENCE) {
  const url = text(evidence.url, 1200)
  let route = url
  try {
    route = new URL(url).pathname
  } catch {
    // 测试或降级输入不是完整 URL 时，直接按原字符串判断路径。
  }
  const pageText = text(evidence.pageText, 5000)
  const submissionState = text(evidence.submissionState, 40) || 'not_submitted'
  if (new RegExp(rules.verificationRoutePattern, 'i').test(route)
    || evidence.hasVisibleChallenge === true
    || new RegExp(rules.verificationTextPattern).test(pageText)) {
    return { code: 'verification_required', message: 'BOSS 要求安全验证。', stopBatch: true, submissionState }
  }
  if (new RegExp(rules.loginRoutePattern, 'i').test(route)
    || new RegExp(rules.loginTextPattern).test(pageText)) {
    return { code: 'login_required', message: 'BOSS 登录状态已失效。', stopBatch: true, submissionState }
  }
  if (new RegExp(rules.rateLimitTextPattern).test(pageText)) {
    return { code: 'rate_limited', message: 'BOSS 限制了当前沟通频率。', stopBatch: true, submissionState }
  }
  return null
}

export function withGreetingRetryContext(result = {}, context = {}) {
  const submissionState = text(result.submissionState, 40) || 'unknown'
  const retryable = submissionState === 'not_submitted'
    || (submissionState === 'failed' && result.code === 'send_failed')
  return {
    ...result,
    submissionState,
    retryable,
    retryMode: retryable ? 'message' : '',
    defaultGreetingSent: context.defaultGreetingSent === true
  }
}

export function isTransientTabEditError(error) {
  const message = error instanceof Error
    ? error.message
    : text(error?.message || error, 500)
  return /Tabs cannot be edited right now/i.test(message)
}

export async function retryTransientTabEdit(operation, options = {}) {
  const delays = Array.isArray(options.delays) && options.delays.length
    ? options.delays.map((value) => Math.max(0, Number(value) || 0))
    : [160, 320, 640]
  const attempts = Math.max(1, Number(options.attempts) || delays.length + 1)
  const waitForRetry = typeof options.wait === 'function'
    ? options.wait
    : (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))

  let lastError
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await operation()
    } catch (error) {
      lastError = error
      if (!isTransientTabEditError(error) || attempt >= attempts - 1) throw error
      await waitForRetry(delays[Math.min(attempt, delays.length - 1)])
    }
  }
  throw lastError
}

export function createTabBusyGreetingFailure(context = {}) {
  return withGreetingRetryContext({
    code: 'browser_tab_busy',
    message: '浏览器正在调整标签页，请稍候后重新发送。',
    stopBatch: true,
    submissionState: 'not_submitted'
  }, context)
}

export function classifyOutgoingMessageEvidence(evidence = {}, rules = CHAT_DELIVERY_EVIDENCE) {
  const statusClasses = text(evidence.statusClasses, 800)
  const statusText = text(evidence.statusText, 300)
  if (new RegExp(rules.failureClassPattern, 'i').test(statusClasses)
    || new RegExp(rules.failureTextPattern).test(statusText)) {
    return 'failed'
  }
  if (new RegExp(rules.successClassPattern, 'i').test(statusClasses)
    || new RegExp(rules.successTextPattern).test(statusText)) {
    return 'sent'
  }
  if (evidence.isNew === true
    && evidence.inputCleared === true
    && Number(evidence.stableForMs) >= Number(rules.stableMs)) {
    return 'sent'
  }
  return 'pending'
}

export function classifyFriendAddResponse(response) {
  const code = Number(response?.code)
  const message = responseMessage(response)
  if (code === 0 && response?.zpData?.showGreeting === true) {
    return { ok: true, defaultGreetingSent: true }
  }
  if (code === 0) return { ok: true }
  if (code === 37) {
    return {
      code: 'login_required',
      message: 'BOSS 登录状态已失效。',
      stopBatch: true,
      retryable: true,
      retryMode: 'full',
      submissionState: 'not_submitted'
    }
  }
  if (/安全验证|验证码|环境存在异常|账号异常/.test(message)) {
    return {
      code: 'verification_required',
      message: message || 'BOSS 要求安全验证。',
      stopBatch: true,
      retryable: true,
      retryMode: 'full',
      submissionState: 'not_submitted'
    }
  }
  if (/操作过于频繁|沟通上限|沟通额度|已与\d+位BOSS沟通|限流/.test(message) || code === 429) {
    return { code: 'rate_limited', message: message || 'BOSS 限制了当前沟通频率。', stopBatch: true }
  }
  if (/职位.*(?:下线|失效|不存在|已关闭)|停止招聘/.test(message)) {
    return { code: 'job_unavailable', message: message || '岗位已失效。', stopBatch: false }
  }
  if (/已经?沟通过|已是好友|已建立沟通/.test(message)) {
    return { code: 'already_contacted', message: message || '此前已与该招聘者沟通过。', stopBatch: false }
  }
  return { code: 'boss_api_error', message: message || 'BOSS 未能建立沟通关系。', stopBatch: true }
}
