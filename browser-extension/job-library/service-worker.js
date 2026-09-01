import {
  buildBossSearchPageUrl,
  buildCityEndpoint,
  buildDetailEndpoint,
  buildSearchEndpoint,
  classifyBossFailure,
  normalizeDetailResponse,
  normalizeCityResponse,
  normalizeSearchResponse,
  shouldSyncBossSearchPage,
  shouldRefreshBossSession
} from './boss-data.js'
import {
  BOSS_CHAT_PAGE_EVIDENCE,
  buildBossChatUrl,
  CHAT_DELIVERY_EVIDENCE,
  classifyFriendAddResponse,
  createTabBusyGreetingFailure,
  isTransientTabEditError,
  normalizeGreetingPayload,
  retryTransientTabEdit,
  withGreetingRetryContext
} from './boss-greeting.js'
import {
  buildXiaohongshuSearchUrl,
  classifyXiaohongshuPageState,
  normalizeXiaohongshuNote,
  normalizeXiaohongshuSearchCards
} from './xiaohongshu-data.js'
import {
  openXiaohongshuNoteFromSearchPage,
  readXiaohongshuNotePage,
  readXiaohongshuSearchPage,
  restoreXiaohongshuSearchPage
} from './xiaohongshu-page.js'
import {
  ASSESSMENT_CAPTURE_CAPABILITY,
  extractAssessmentFromPage,
  normalizeAssessmentCapture
} from './assessment-capture.js'

const WEB_CHANNEL = 'find-job-job-library-web-v1'
const GREETING_CAPABILITIES = Object.freeze([
  'retry_greeting_message',
  'greeting_submission_state',
  'greeting_fire_and_continue',
  'xiaohongshu_keyword_collection',
  ASSESSMENT_CAPTURE_CAPABILITY
])
const PACKAGED_APP_ORIGIN = ''
const DEFAULT_APP_ORIGIN = PACKAGED_APP_ORIGIN || 'http://127.0.0.1'
const APP_ORIGINS = [...new Set(['http://127.0.0.1', 'http://localhost', DEFAULT_APP_ORIGIN])]
const ALLOWED_APP_HOSTS = new Set(APP_ORIGINS.map((origin) => new URL(origin).hostname))
const APP_PAGE_PATTERNS = APP_ORIGINS.map((origin) => `${origin}/*`)
const BOSS_HOME = 'https://www.zhipin.com/web/geek/jobs?city=101020100&ka=open_joblist'
const REQUEST_GAP_MS = 1000
const SESSION_SETTLE_MS = 1500
const XIAOHONGSHU_HOME = 'https://www.xiaohongshu.com/explore'
const XIAOHONGSHU_DETAIL_GAP_MS = 5000
let requestQueue = Promise.resolve()
let lastRequestAt = 0
let greetingTabId = null
let lastXiaohongshuDetailAt = 0
let xiaohongshuSearchContext = null
const ASSESSMENT_LAUNCH_TTL_MS = 5 * 60 * 1000
const ASSESSMENT_BINDING_TTL_MS = 2 * 60 * 60 * 1000
const ASSESSMENT_MAX_SCREENSHOT_DATA_URL = 7_000_000
let assessmentAssistantWindowId = null

function isAllowedSender(sender) {
  try {
    return ALLOWED_APP_HOSTS.has(new URL(sender.url ?? '').hostname)
  } catch {
    return false
  }
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function waitForTabReady(tabId, timeoutMs = 15000) {
  const current = await chrome.tabs.get(tabId)
  if (current.status === 'complete') return current

  return new Promise((resolve, reject) => {
    const timeoutId = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener)
      reject(new Error('BOSS 页面加载超时，请检查网络后重试。'))
    }, timeoutMs)
    const listener = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId !== tabId || changeInfo.status !== 'complete') return
      clearTimeout(timeoutId)
      chrome.tabs.onUpdated.removeListener(listener)
      resolve(tab)
    }
    chrome.tabs.onUpdated.addListener(listener)
  })
}

async function getBossTab(initialUrl = BOSS_HOME) {
  const tabs = await chrome.tabs.query({ url: 'https://www.zhipin.com/*' })
  const prioritizedTabs = tabs
    .filter((tab) => tab.id)
    .sort((left, right) => {
      const searchPageDifference = Number(isBossSearchPage(right.url)) - Number(isBossSearchPage(left.url))
      if (searchPageDifference) return searchPageDifference
      const activeDifference = Number(Boolean(right.active)) - Number(Boolean(left.active))
      if (activeDifference) return activeDifference
      const availableDifference = Number(!right.discarded) - Number(!left.discarded)
      if (availableDifference) return availableDifference
      return Number(right.lastAccessed ?? 0) - Number(left.lastAccessed ?? 0)
    })
  const readyTab = prioritizedTabs.find((tab) => tab.status === 'complete')
  if (readyTab) return readyTab
  const pendingTab = prioritizedTabs[0]
  if (pendingTab?.id) return waitForTabReady(pendingTab.id)

  const created = await chrome.tabs.create({ url: initialUrl, active: false })
  if (!created.id) throw new Error('无法打开 BOSS 直聘后台页面。')
  return waitForTabReady(created.id)
}

async function getXiaohongshuTab(initialUrl = XIAOHONGSHU_HOME) {
  const tabs = await chrome.tabs.query({ url: 'https://www.xiaohongshu.com/*' })
  const reusable = tabs
    .filter((tab) => tab.id && !tab.active)
    .sort((left, right) => Number(right.lastAccessed ?? 0) - Number(left.lastAccessed ?? 0))[0]
  if (reusable?.id) {
    return reusable.status === 'complete' ? reusable : waitForTabReady(reusable.id, 20000)
  }
  const created = await chrome.tabs.create({ url: initialUrl, active: false })
  if (!created.id) throw new Error('无法打开小红书后台页面。')
  return waitForTabReady(created.id, 20000)
}

async function navigateXiaohongshuTab(tabId, url) {
  const parsed = new URL(url)
  if (parsed.protocol !== 'https:' || !parsed.hostname.endsWith('xiaohongshu.com')) {
    throw new Error('小红书页面地址不正确。')
  }
  const targetUrl = parsed.toString()
  const current = await chrome.tabs.get(tabId)
  if (current.status === 'complete' && current.url === targetUrl) return current
  const ready = new Promise((resolve, reject) => {
    const timeoutId = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener)
      reject(new Error('小红书页面加载超时，请检查网络后重试。'))
    }, 25000)
    const listener = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId !== tabId || changeInfo.status !== 'complete') return
      clearTimeout(timeoutId)
      chrome.tabs.onUpdated.removeListener(listener)
      resolve(tab)
    }
    chrome.tabs.onUpdated.addListener(listener)
  })
  await retryTransientTabEdit(() => chrome.tabs.update(tabId, { url: targetUrl, active: false }))
  return ready
}

async function runXiaohongshuPageReader(tabId, reader, args = []) {
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    func: reader,
    args
  })
  return result ?? {}
}

async function activateXiaohongshuTab(tabId) {
  try {
    const tab = await chrome.tabs.update(tabId, { active: true })
    if (tab.windowId) await chrome.windows.update(tab.windowId, { focused: true })
  } catch {
    // 无法聚焦时仍返回原始可操作错误，不以窗口状态覆盖真正原因。
  }
}

async function restoreXiaohongshuSearchContext(tabId, searchUrl) {
  try {
    const restored = await runXiaohongshuPageReader(
      tabId,
      restoreXiaohongshuSearchPage,
      [searchUrl]
    )
    if (restored.restored) return
  } catch {
    // 完整页面导航会销毁注入上下文，随后使用普通搜索页导航恢复。
  }
  await navigateXiaohongshuTab(tabId, searchUrl)
}

async function getXiaohongshuSearchContext() {
  const context = xiaohongshuSearchContext
  if (!context?.tabId || !context.searchUrl) return null
  try {
    const tab = await chrome.tabs.get(context.tabId)
    if (!tab.url?.startsWith('https://www.xiaohongshu.com/')) return null
    if (tab.status !== 'complete') await waitForTabReady(tab.id, 20000)
    return context
  } catch {
    return null
  }
}

async function injectBridgeIntoOpenAppTabs() {
  const tabs = await chrome.tabs.query({ url: APP_PAGE_PATTERNS })
  await Promise.allSettled(
    tabs.filter((tab) => tab.id).map((tab) => chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['content-script.js']
    }))
  )
}

async function handleXiaohongshuOperation(action, payload) {
  if (action === 'search_xiaohongshu_notes') {
    let searchUrl
    try {
      searchUrl = buildXiaohongshuSearchUrl(payload.keyword)
    } catch (error) {
      return { ok: false, error: { code: 'invalid_keyword', message: error.message } }
    }
    const limit = Math.max(5, Math.min(50, Number(payload.limit) || 20))
    const tab = await getXiaohongshuTab(searchUrl)
    await navigateXiaohongshuTab(tab.id, searchUrl)
    const snapshot = await runXiaohongshuPageReader(tab.id, readXiaohongshuSearchPage, [limit])
    const failure = classifyXiaohongshuPageState(snapshot)
    if (failure) {
      await activateXiaohongshuTab(tab.id)
      return { ok: false, error: failure }
    }
    const cards = normalizeXiaohongshuSearchCards(snapshot.cards, limit)
    if (!cards.length) {
      return {
        ok: false,
        error: {
          code: 'search_results_empty',
          message: '小红书搜索页没有返回可读取的笔记卡片，请检查关键词或页面状态。'
        }
      }
    }
    xiaohongshuSearchContext = { tabId: tab.id, searchUrl }
    return { ok: true, data: { keyword: String(payload.keyword).trim(), cards } }
  }

  const signedUrl = String(payload.signedUrl ?? '').trim()
  const noteId = String(payload.noteId ?? '').trim()
  let parsed
  try {
    parsed = new URL(signedUrl)
  } catch {
    return { ok: false, error: { code: 'invalid_note_url', message: '小红书笔记临时地址不正确。' } }
  }
  if (parsed.protocol !== 'https:' || !parsed.hostname.endsWith('xiaohongshu.com') || !noteId) {
    return { ok: false, error: { code: 'invalid_note_url', message: '小红书笔记临时地址不正确。' } }
  }
  const context = await getXiaohongshuSearchContext()
  if (!context) {
    return {
      ok: false,
      error: {
        code: 'search_context_lost',
        message: '小红书搜索页上下文已失效，任务已暂停；请点击继续收集以重新搜索。',
        stopBatch: true,
        retryable: true
      }
    }
  }
  const gap = Date.now() - lastXiaohongshuDetailAt
  if (gap < XIAOHONGSHU_DETAIL_GAP_MS) await wait(XIAOHONGSHU_DETAIL_GAP_MS - gap)
  lastXiaohongshuDetailAt = Date.now()

  const opened = await runXiaohongshuPageReader(
    context.tabId,
    openXiaohongshuNoteFromSearchPage,
    [noteId]
  )
  if (!opened.clicked) {
    return {
      ok: false,
      error: {
        code: 'note_card_unavailable',
        message: '搜索页中的这张卡片已不可用，已跳过并继续处理下一篇。',
        stopBatch: false,
        retryable: false
      }
    }
  }

  await wait(900)
  const current = await chrome.tabs.get(context.tabId)
  if (current.status !== 'complete') await waitForTabReady(context.tabId, 20000)
  const snapshot = await runXiaohongshuPageReader(
    context.tabId,
    readXiaohongshuNotePage,
    [noteId]
  )
  const failure = classifyXiaohongshuPageState(snapshot)
  if (failure?.stopBatch) {
    await activateXiaohongshuTab(context.tabId)
    return { ok: false, error: failure }
  }
  try {
    if (failure) return { ok: false, error: failure }
    return { ok: true, data: normalizeXiaohongshuNote(snapshot, payload) }
  } catch (error) {
    return { ok: false, error: { code: 'note_parse_failed', message: error.message } }
  } finally {
    try {
      await restoreXiaohongshuSearchContext(context.tabId, context.searchUrl)
    } catch {
      xiaohongshuSearchContext = null
    }
  }
}

function isBossSearchPage(url) {
  try {
    return new URL(url ?? '').pathname === '/web/geek/jobs'
  } catch {
    return false
  }
}

function isBossBlockedPage(url) {
  try {
    const pathname = new URL(url ?? '').pathname
    return /verify|security-check/i.test(pathname) || /\/web\/user\/?$/.test(pathname)
  } catch {
    return false
  }
}

async function navigateBossTab(tabId, url, timeoutMs = 20000) {
  let listener
  let timeoutId
  const ready = new Promise((resolve, reject) => {
    listener = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId !== tabId || changeInfo.status !== 'complete') return
      clearTimeout(timeoutId)
      chrome.tabs.onUpdated.removeListener(listener)
      resolve(tab)
    }
    timeoutId = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener)
      reject(new Error('BOSS 城市页面加载超时。'))
    }, timeoutMs)
    chrome.tabs.onUpdated.addListener(listener)
  })

  try {
    await retryTransientTabEdit(() => chrome.tabs.update(tabId, { url, active: false }))
  } catch (error) {
    clearTimeout(timeoutId)
    chrome.tabs.onUpdated.removeListener(listener)
    throw error
  }

  const tab = await ready
  await wait(SESSION_SETTLE_MS)
  return tab
}

async function syncBossSearchContext(tab, payload) {
  if (!tab?.id || isBossBlockedPage(tab.url)) return tab
  const cityCode = String(payload.cityCode ?? '').trim()
  if (!shouldSyncBossSearchPage(tab.url, cityCode)) return tab

  const targetUrl = buildBossSearchPageUrl(payload.query, cityCode)
  let syncedTab
  if (isBossSearchPage(tab.url)) {
    syncedTab = await navigateBossTab(tab.id, targetUrl)
  } else {
    const created = await chrome.tabs.create({ url: targetUrl, active: false })
    if (!created.id) throw new Error('无法打开所选城市的 BOSS 搜索页面。')
    syncedTab = await waitForTabReady(created.id, 20000)
    await wait(SESSION_SETTLE_MS)
  }

  return syncedTab
}

async function reloadBossTab(tabId, timeoutMs = 20000) {
  let listener
  let timeoutId
  const ready = new Promise((resolve, reject) => {
    listener = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId !== tabId || changeInfo.status !== 'complete') return
      clearTimeout(timeoutId)
      chrome.tabs.onUpdated.removeListener(listener)
      resolve(tab)
    }
    timeoutId = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener)
      reject(new Error('BOSS 页面自动刷新超时。'))
    }, timeoutMs)
    chrome.tabs.onUpdated.addListener(listener)
  })

  try {
    await chrome.tabs.reload(tabId, { bypassCache: false })
  } catch (error) {
    clearTimeout(timeoutId)
    chrome.tabs.onUpdated.removeListener(listener)
    throw error
  }

  const tab = await ready
  await wait(SESSION_SETTLE_MS)
  return tab
}

async function respectRequestGap() {
  const elapsed = Date.now() - lastRequestAt
  if (elapsed < REQUEST_GAP_MS) await wait(REQUEST_GAP_MS - elapsed)
}

async function runBossFetch(tabId, endpoint) {
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    args: [endpoint],
    func: async (requestedEndpoint) => {
      const pageText = document.body?.innerText ?? ''
      if (/安全验证|环境存在异常|请稍候/.test(pageText) || /verify|security-check/i.test(location.pathname)) {
        return { kind: 'verification' }
      }
      if (/登录后继续|扫码登录|密码登录/.test(pageText) || /\/web\/user\/?$/.test(location.pathname)) {
        return { kind: 'login' }
      }

      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 12000)
      try {
        const supportedEndpoint = /^\/wapi\/zpgeek\/(search\/joblist|job\/detail)\.json\?/.test(requestedEndpoint)
          || requestedEndpoint === '/wapi/zpgeek/common/data/city/site.json'
        if (!supportedEndpoint) {
          return { kind: 'network', message: '不支持的职位操作。' }
        }

        const response = await fetch(requestedEndpoint, {
          method: 'GET',
          credentials: 'include',
          cache: 'no-store',
          headers: { Accept: 'application/json, text/plain, */*' },
          signal: controller.signal
        })
        if (!response.ok) return { kind: 'http', status: response.status }
        return { kind: 'api', response: await response.json() }
      } catch (error) {
        return {
          kind: 'network',
          message: error instanceof Error && error.name === 'AbortError'
            ? 'BOSS 直聘请求超时，请稍后重试。'
            : (error instanceof Error ? error.message : 'BOSS 直聘请求失败。')
        }
      } finally {
        clearTimeout(timeoutId)
      }
    }
  })
  return result
}

async function runBossReadWithSingleRefresh(tabId, endpoint, action) {
  await respectRequestGap()
  let result = await runBossFetch(tabId, endpoint)
  lastRequestAt = Date.now()
  let failure = classifyBossFailure(result)
  if (!shouldRefreshBossSession(failure, action)) return { result, failure, refreshed: false }

  try {
    await reloadBossTab(tabId)
  } catch {
    return {
      result: null,
      failure: {
        code: 'session_refresh_failed',
        message: 'BOSS 页面自动刷新未完成，本次未执行发送或写入，可安全重试。',
        retryable: true
      },
      refreshed: true
    }
  }

  await respectRequestGap()
  result = await runBossFetch(tabId, endpoint)
  lastRequestAt = Date.now()
  failure = classifyBossFailure(result)
  if (shouldRefreshBossSession(failure, action)) {
    return {
      result,
      failure: {
        code: 'session_refresh_failed',
        message: 'BOSS 页面已自动刷新一次，但会话状态仍未更新；本次未执行发送或写入，可安全重试。',
        retryable: true
      },
      refreshed: true
    }
  }
  return { result, failure, refreshed: true }
}

async function runFriendAdd(tabId, payload) {
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    args: [payload],
    func: async (job) => {
      const pageText = document.body?.innerText ?? ''
      if (/安全验证|环境存在异常|请稍候/.test(pageText) || /verify|security-check/i.test(location.pathname)) {
        return { kind: 'verification' }
      }
      if (/登录后继续|扫码登录|密码登录/.test(pageText) || /\/web\/user\/?$/.test(location.pathname)) {
        return { kind: 'login' }
      }

      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 12000)
      try {
        const tokenResponse = await fetch('/wapi/zppassport/get/zpToken', {
          credentials: 'include',
          cache: 'no-store',
          signal: controller.signal
        })
        if (!tokenResponse.ok) return { kind: 'http', status: tokenResponse.status }
        const tokenPayload = await tokenResponse.json()
        const token = String(tokenPayload?.zpData?.token ?? '').trim()
        if (!token) return { kind: 'login' }

        const endpoint = new URL('/wapi/zpgeek/friend/add.json', location.origin)
        endpoint.search = new URLSearchParams({
          securityId: job.securityId,
          lid: job.lid,
          jobId: job.jobId
        }).toString()
        const response = await fetch(endpoint, {
          method: 'POST',
          credentials: 'include',
          cache: 'no-store',
          headers: {
            Accept: 'application/json, text/plain, */*',
            zp_token: token
          },
          signal: controller.signal
        })
        if (!response.ok) return { kind: 'http', status: response.status }
        return { kind: 'api', response: await response.json() }
      } catch (error) {
        return {
          kind: 'network',
          message: error instanceof Error && error.name === 'AbortError'
            ? 'BOSS 建立沟通请求超时。'
            : (error instanceof Error ? error.message : 'BOSS 建立沟通请求失败。')
        }
      } finally {
        clearTimeout(timeoutId)
      }
    }
  })
  return result
}

async function getGreetingTab(url) {
  if (greetingTabId) {
    try {
      const current = await chrome.tabs.get(greetingTabId)
      if (current?.id) return navigateBossTab(current.id, url, 25000)
    } catch (error) {
      if (isTransientTabEditError(error)) throw error
      greetingTabId = null
    }
  }
  const created = await retryTransientTabEdit(() => chrome.tabs.create({ url, active: false }))
  if (!created.id) throw new Error('无法打开 BOSS 聊天页面。')
  greetingTabId = created.id
  return waitForTabReady(created.id, 25000)
}

async function runChatSend(tabId, message) {
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    args: [message, CHAT_DELIVERY_EVIDENCE, BOSS_CHAT_PAGE_EVIDENCE],
    func: async (finalMessage, evidenceRules, pageEvidenceRules) => {
      const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))
      const messageToSend = String(finalMessage ?? '').trim()
      const normalizeMessageText = (value) => String(value ?? '')
        .replace(/[\u200B-\u200D\u2060\uFEFF]/g, '')
        .replace(/\u00A0/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 4000)
      const normalized = normalizeMessageText(messageToSend)
      const outgoingRowSelector = [
        '.item-myself',
        '.message-item.myself',
        '.message-item.is-self',
        '.chat-message.myself',
        '.chat-message.is-self',
        '[class~="message-item"][class~="myself"]',
        '[data-message-direction="outgoing"]',
        '[data-direction="outgoing"]',
        '[data-side="right"][class*="message"]'
      ].join(',')
      const messageTextSelectors = [
        '.message-text',
        '[data-message-text]',
        '[class*="message-content"]',
        '[class*="message-bubble"]'
      ]
      const readMessageText = (row) => {
        for (const selector of messageTextSelectors) {
          const candidate = row.querySelector(selector)
          const candidateText = normalizeMessageText(candidate?.textContent)
          if (candidateText) return candidateText
        }
        return ''
      }
      const outgoingMessages = () => [...new Set(document.querySelectorAll(outgoingRowSelector))]
        .map((row) => ({ row, text: readMessageText(row) }))
        .filter((item) => item.text)
      const classifyEvidence = (evidence = {}) => {
        const statusClasses = String(evidence.statusClasses ?? '').trim().slice(0, 800)
        const statusText = String(evidence.statusText ?? '').trim().slice(0, 300)
        if (new RegExp(evidenceRules.failureClassPattern, 'i').test(statusClasses)
          || new RegExp(evidenceRules.failureTextPattern).test(statusText)) {
          return 'failed'
        }
        if (new RegExp(evidenceRules.successClassPattern, 'i').test(statusClasses)
          || new RegExp(evidenceRules.successTextPattern).test(statusText)) {
          return 'sent'
        }
        if (evidence.isNew === true
          && evidence.inputCleared === true
          && Number(evidence.stableForMs) >= Number(evidenceRules.stableMs)) {
          return 'sent'
        }
        return 'pending'
      }
      const isVisible = (element) => {
        if (!element) return false
        const style = window.getComputedStyle(element)
        const rect = element.getBoundingClientRect()
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
      }
      const pageState = (submissionState = 'not_submitted') => {
        const pageText = document.body?.innerText ?? ''
        const pageRoute = location.pathname
        const hasVisibleChallenge = [...document.querySelectorAll(pageEvidenceRules.verificationSelector)]
          .some(isVisible)
        if (new RegExp(pageEvidenceRules.verificationRoutePattern, 'i').test(pageRoute)
          || hasVisibleChallenge
          || new RegExp(pageEvidenceRules.verificationTextPattern).test(pageText)) {
          return { code: 'verification_required', message: 'BOSS 要求安全验证。', stopBatch: true, submissionState }
        }
        if (new RegExp(pageEvidenceRules.loginTextPattern).test(pageText)
          || new RegExp(pageEvidenceRules.loginRoutePattern, 'i').test(pageRoute)) {
          return { code: 'login_required', message: 'BOSS 登录状态已失效。', stopBatch: true, submissionState }
        }
        if (new RegExp(pageEvidenceRules.rateLimitTextPattern).test(pageText)) {
          return { code: 'rate_limited', message: 'BOSS 限制了当前沟通频率。', stopBatch: true, submissionState }
        }
        return null
      }
      const currentPageState = pageState()
      if (currentPageState) return currentPageState

      let chatInput = null
      let sendButton = null
      for (let attempt = 0; attempt < 200; attempt += 1) {
        const blocked = pageState()
        if (blocked) return blocked
        chatInput = document.querySelector('#chat-input')
        sendButton = document.querySelector('.chat-op .btn-send')
          || [...document.querySelectorAll('button,a')].find((element) => element.textContent?.trim() === '发送')
        if (chatInput && sendButton) break
        await sleep(100)
      }
      if (!chatInput || !sendButton) {
        return { code: 'chat_unavailable', message: 'BOSS 聊天输入框或发送按钮未加载。', stopBatch: true, submissionState: 'not_submitted' }
      }

      if (outgoingMessages().some(({ row, text }) => {
        if (text !== normalized) return false
        const status = row?.querySelector('.status,[class*="status-"]')
        return classifyEvidence({
          statusClasses: `${row?.className ?? ''} ${status?.className ?? ''}`,
          statusText: status?.textContent ?? ''
        }) === 'sent'
      })) {
        return { ok: true, status: 'already_sent' }
      }

      chatInput.focus()
      const selection = window.getSelection()
      const range = document.createRange()
      range.selectNodeContents(chatInput)
      selection?.removeAllRanges()
      selection?.addRange(range)
      let inserted = false
      try {
        inserted = document.execCommand('insertText', false, messageToSend)
      } catch {
        inserted = false
      }
      if (!inserted || normalizeMessageText(chatInput.textContent) !== normalized) {
        chatInput.textContent = messageToSend
      }
      chatInput.dispatchEvent(new InputEvent('input', {
        bubbles: true,
        inputType: 'insertText',
        data: messageToSend
      }))
      chatInput.dispatchEvent(new Event('change', { bubbles: true }))
      await sleep(180)
      if (normalizeMessageText(chatInput.textContent) !== normalized) {
        return { code: 'message_fill_failed', message: '招呼语未能写入 BOSS 聊天框。', stopBatch: true, submissionState: 'not_submitted' }
      }
      if (sendButton.disabled || sendButton.getAttribute('aria-disabled') === 'true') {
        return { code: 'send_disabled', message: 'BOSS 发送按钮当前不可用。', stopBatch: true, submissionState: 'not_submitted' }
      }

      sendButton.click()
      return { ok: true, status: 'submitted' }
    }
  })
  return result
}

function greetingFailure(result) {
  if (!result) return { code: 'empty_response', message: 'BOSS 没有返回发送结果。', stopBatch: true }
  if (result.kind === 'verification') return { code: 'verification_required', message: 'BOSS 要求安全验证。', stopBatch: true }
  if (result.kind === 'login') return { code: 'login_required', message: '请先登录 BOSS。', stopBatch: true }
  if (result.kind === 'network') return { code: 'network_error', message: result.message || 'BOSS 网络请求失败。', stopBatch: true }
  if (result.kind === 'http') {
    return Number(result.status) === 429
      ? { code: 'rate_limited', message: 'BOSS 限制了当前沟通频率。', stopBatch: true }
      : { code: 'boss_http_error', message: `BOSS 返回 HTTP ${result.status || '异常状态'}。`, stopBatch: true }
  }
  return null
}

async function handleGreetingOperation(action, rawPayload) {
  let payload
  try {
    payload = normalizeGreetingPayload(rawPayload)
  } catch (error) {
    return { ok: false, error: { code: 'invalid_greeting', message: error.message, stopBatch: true } }
  }

  const bossTab = await getBossTab(BOSS_HOME)
  if (!bossTab?.id || isBossBlockedPage(bossTab.url)) {
    return {
      ok: false,
      error: {
        code: 'login_or_verification_required',
        message: '请检查 BOSS 登录或安全验证状态。',
        stopBatch: true,
        retryable: true,
        retryMode: 'full',
        submissionState: 'not_submitted'
      }
    }
  }

  const detailRead = await runBossReadWithSingleRefresh(
    bossTab.id,
    buildDetailEndpoint(payload.securityId),
    'get_job_detail'
  )
  const detailResult = detailRead.result
  const detailFailure = detailRead.failure
  if (detailFailure) return { ok: false, error: { ...detailFailure, stopBatch: true } }
  const detail = normalizeDetailResponse(detailResult.response, payload)
  if (!detail.jobId || !detail.bossId || !detail.lid) {
    return { ok: false, error: { code: 'job_identifiers_missing', message: '岗位缺少聊天标识，请重新搜索并选择岗位。', stopBatch: true } }
  }
  if (action === 'preflight_greeting') {
    return { ok: true, data: { ready: true, job: { securityId: detail.securityId, jobId: detail.jobId, bossId: detail.bossId, lid: detail.lid } } }
  }

  let friendState = {
    ok: true,
    defaultGreetingSent: rawPayload?.defaultGreetingSent === true
  }
  if (action !== 'retry_greeting_message') {
    await respectRequestGap()
    const friendResult = await runFriendAdd(bossTab.id, detail)
    lastRequestAt = Date.now()
    const transportFailure = greetingFailure(friendResult)
    if (transportFailure) return { ok: false, error: transportFailure }
    friendState = classifyFriendAddResponse(friendResult.response)
    if (!friendState.ok) {
      if (friendState.stopBatch === false) {
        return { ok: true, data: { status: 'skipped', reason: friendState.message, code: friendState.code } }
      }
      return { ok: false, error: friendState }
    }
  }

  let chatTab
  try {
    chatTab = await getGreetingTab(buildBossChatUrl({ ...detail, message: payload.message }))
  } catch (error) {
    if (!isTransientTabEditError(error)) throw error
    return {
      ok: false,
      error: createTabBusyGreetingFailure({
        defaultGreetingSent: friendState.defaultGreetingSent === true
      })
    }
  }
  const sendResult = await runChatSend(chatTab.id, payload.message)
  if (!sendResult?.ok) {
    return {
      ok: false,
      error: withGreetingRetryContext(
        sendResult || { code: 'send_unknown', message: '发送结果未知。', stopBatch: true, submissionState: 'unknown' },
        { defaultGreetingSent: friendState.defaultGreetingSent === true }
      )
    }
  }
  return {
    ok: true,
    data: {
      status: sendResult.status,
      defaultGreetingSent: friendState.defaultGreetingSent === true,
      resumedMessageOnly: action === 'retry_greeting_message',
      sentAt: new Date().toISOString()
    }
  }
}

async function handleBossOperation(action, payload) {
  const endpoint = action === 'search_jobs'
    ? buildSearchEndpoint(payload.query, payload.page, payload.cityCode)
    : action === 'list_cities'
      ? buildCityEndpoint()
      : buildDetailEndpoint(payload.securityId)
  const initialUrl = action === 'search_jobs'
    ? buildBossSearchPageUrl(payload.query, payload.cityCode)
    : BOSS_HOME
  let tab = await getBossTab(initialUrl)
  if (action === 'search_jobs') {
    try {
      tab = await syncBossSearchContext(tab, payload)
    } catch {
      return {
        ok: false,
        error: {
          code: 'city_context_sync_failed',
          message: '未能把 BOSS 页面切换到所选城市。助手已停止本次搜索，请检查 BOSS 页面后重试。'
        }
      }
    }
  }
  const read = await runBossReadWithSingleRefresh(tab.id, endpoint, action)
  const { result, failure } = read

  if (failure) return { ok: false, error: failure }

  if (action === 'list_cities') {
    return { ok: true, data: normalizeCityResponse(result.response) }
  }
  if (action === 'search_jobs') {
    const data = normalizeSearchResponse(result.response, {
      query: payload.query,
      cityCode: String(payload.cityCode ?? '').trim(),
      cityName: String(payload.cityName ?? '').trim() || '上海',
      page: payload.page
    })
    if (data.rawJobCount > 0 && data.jobs.length === 0 && data.rejectedCityCount === data.rawJobCount) {
      return {
        ok: false,
        error: {
          code: 'city_mismatch',
          message: `BOSS 返回的岗位城市与“${data.city}”不一致，已停止展示。请稍后重新搜索或打开 BOSS 页面确认城市。`
        }
      }
    }
    return {
      ok: true,
      data
    }
  }
  return {
    ok: true,
    data: normalizeDetailResponse(result.response, payload.fallback ?? {})
  }
}

function randomAssessmentToken() {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('')
}

function assessmentStorageKey(kind, token) {
  return `online-assessment:${kind}:${token}`
}

async function captureAssessmentTab(tabId, windowId) {
  const tab = await chrome.tabs.get(tabId)
  if (!tab?.id || !/^https?:/i.test(tab.url ?? '')) {
    throw Object.assign(new Error('当前页面不支持题面识别，请打开在线笔试题目页后重试。'), { code: 'unsupported_source_page' })
  }
  const activeTabs = await chrome.tabs.query({ active: true, windowId })
  if (activeTabs[0]?.id !== tabId) {
    throw Object.assign(new Error('请先切回原题目标签页，再点击重新识别。'), { code: 'source_tab_not_visible' })
  }
  let executions
  try {
    executions = await chrome.scripting.executeScript({
      target: { tabId },
      func: extractAssessmentFromPage
    })
  } catch (error) {
    const detail = error instanceof Error && error.message ? `：${error.message}` : ''
    throw Object.assign(new Error(`题面读取脚本执行失败${detail}`), { code: 'assessment_capture_script_failed' })
  }
  const execution = executions?.[0]
  if (execution?.error) {
    const detail = typeof execution.error === 'string'
      ? execution.error
      : execution.error?.message
    throw Object.assign(
      new Error(`题面读取脚本执行失败${detail ? `：${detail}` : ''}`),
      { code: 'assessment_capture_script_failed' }
    )
  }
  const result = execution?.result
  if (!result || typeof result !== 'object') {
    throw Object.assign(new Error('当前页面没有返回可识别的题面内容。'), { code: 'empty_assessment_capture' })
  }
  const capture = normalizeAssessmentCapture(result)
  let screenshotDataUrl = ''
  try {
    const screenshot = await chrome.tabs.captureVisibleTab(windowId, { format: 'png' })
    if (typeof screenshot === 'string' && screenshot.length <= ASSESSMENT_MAX_SCREENSHOT_DATA_URL) {
      screenshotDataUrl = screenshot
    }
  } catch {
    screenshotDataUrl = ''
  }
  return {
    ...capture,
    screenshotDataUrl,
    capturedAt: new Date().toISOString()
  }
}

async function resolveAssessmentAppOrigin() {
  const tabs = await chrome.tabs.query({ url: APP_PAGE_PATTERNS })
  const candidate = tabs
    .filter((tab) => /^https?:/i.test(tab.url ?? ''))
    .sort((left, right) => {
      const activeDifference = Number(Boolean(right.active)) - Number(Boolean(left.active))
      if (activeDifference) return activeDifference
      return Number(right.lastAccessed ?? 0) - Number(left.lastAccessed ?? 0)
    })[0]
  if (candidate?.url) return new URL(candidate.url).origin
  return DEFAULT_APP_ORIGIN
}

async function storeAssessmentLaunch(tab, capture) {
  const launchId = randomAssessmentToken()
  const now = Date.now()
  await chrome.storage.session.set({
    [assessmentStorageKey('launch', launchId)]: {
      launchId,
      sourceTabId: tab.id,
      sourceWindowId: tab.windowId,
      createdAt: now,
      expiresAt: now + ASSESSMENT_LAUNCH_TTL_MS,
      capture
    }
  })
  return launchId
}

async function openAssessmentAssistant(launchId) {
  const origin = await resolveAssessmentAppOrigin()
  const url = new URL('/career/online-assessment', origin)
  url.searchParams.set('launch_id', launchId)
  if (assessmentAssistantWindowId) {
    try {
      const existing = await chrome.windows.get(assessmentAssistantWindowId, { populate: true })
      const tabId = existing.tabs?.[0]?.id
      if (tabId) await chrome.tabs.update(tabId, { url: url.toString() })
      await chrome.windows.update(assessmentAssistantWindowId, { focused: true })
      return
    } catch {
      assessmentAssistantWindowId = null
    }
  }
  const created = await chrome.windows.create({
    url: url.toString(),
    type: 'popup',
    focused: true,
    width: 1460,
    height: 940
  })
  assessmentAssistantWindowId = created.id ?? null
}

async function claimAssessmentLaunch(launchId) {
  const normalizedId = String(launchId ?? '').trim()
  if (!/^[0-9a-f]{32}$/.test(normalizedId)) {
    return { ok: false, error: { code: 'invalid_launch_id', message: '题面领取标识无效，请回到题目页重新点击扩展。' } }
  }
  const key = assessmentStorageKey('launch', normalizedId)
  const stored = (await chrome.storage.session.get(key))[key]
  await chrome.storage.session.remove(key)
  if (!stored || Number(stored.expiresAt) <= Date.now()) {
    return { ok: false, error: { code: 'assessment_launch_expired', message: '本次题面识别已过期，请回到题目页重新点击扩展。' } }
  }
  const captureToken = randomAssessmentToken()
  await chrome.storage.session.set({
    [assessmentStorageKey('binding', captureToken)]: {
      sourceTabId: stored.sourceTabId,
      sourceWindowId: stored.sourceWindowId,
      expiresAt: Date.now() + ASSESSMENT_BINDING_TTL_MS
    }
  })
  return {
    ok: true,
    data: {
      captureToken,
      sourceTabId: stored.sourceTabId,
      capture: stored.capture
    }
  }
}

async function recaptureAssessment(captureToken) {
  const normalizedToken = String(captureToken ?? '').trim()
  const key = assessmentStorageKey('binding', normalizedToken)
  const binding = (await chrome.storage.session.get(key))[key]
  if (!binding || Number(binding.expiresAt) <= Date.now()) {
    await chrome.storage.session.remove(key)
    return { ok: false, error: { code: 'assessment_binding_expired', message: '题目标签页连接已过期，请重新点击扩展图标。' } }
  }
  try {
    const capture = await captureAssessmentTab(binding.sourceTabId, binding.sourceWindowId)
    return { ok: true, data: { capture, captureToken: normalizedToken, sourceTabId: binding.sourceTabId } }
  } catch (error) {
    return {
      ok: false,
      error: {
        code: error?.code || 'assessment_capture_failed',
        message: error instanceof Error ? error.message : '题面重新识别失败。'
      }
    }
  }
}

async function handleAssessmentOperation(action, payload) {
  if (action === 'claim_assessment_launch') return claimAssessmentLaunch(payload?.launchId)
  if (action === 'refresh_assessment_problem' || action === 'append_assessment_problem') {
    return recaptureAssessment(payload?.captureToken)
  }
  return { ok: false, error: { code: 'unsupported_assessment_action', message: '不支持的题面操作。' } }
}

async function handleMessage(message, sender) {
  if (!isAllowedSender(sender) || message?.channel !== WEB_CHANNEL) {
    return { ok: false, error: { code: 'forbidden_sender', message: '当前页面不能调用职位库助手。' } }
  }
  if (message.action === 'ping') {
    return {
      ok: true,
      data: {
        connected: true,
        version: chrome.runtime.getManifest().version,
        capabilities: GREETING_CAPABILITIES
      }
    }
  }
  if (['claim_assessment_launch', 'refresh_assessment_problem', 'append_assessment_problem'].includes(message.action)) {
    return handleAssessmentOperation(message.action, message.payload ?? {})
  }
  if (['preflight_greeting', 'send_greeting', 'retry_greeting_message'].includes(message.action)) {
    return handleGreetingOperation(message.action, message.payload ?? {})
  }
  if (['search_xiaohongshu_notes', 'get_xiaohongshu_note'].includes(message.action)) {
    return handleXiaohongshuOperation(message.action, message.payload ?? {})
  }
  if (!['list_cities', 'search_jobs', 'get_job_detail'].includes(message.action)) {
    return { ok: false, error: { code: 'unsupported_action', message: '职位库助手不支持该操作。' } }
  }
  return handleBossOperation(message.action, message.payload ?? {})
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  requestQueue = requestQueue
    .catch(() => undefined)
    .then(() => handleMessage(message, sender))
    .then(sendResponse)
    .catch((error) => sendResponse({
      ok: false,
      error: {
        code: 'extension_error',
        message: error instanceof Error ? error.message : '职位库助手运行失败。'
      }
    }))
  return true
})

chrome.runtime.onInstalled.addListener(() => {
  void injectBridgeIntoOpenAppTabs()
})

chrome.runtime.onStartup.addListener(() => {
  void injectBridgeIntoOpenAppTabs()
})

chrome.windows.onRemoved.addListener((windowId) => {
  if (windowId === assessmentAssistantWindowId) assessmentAssistantWindowId = null
})

chrome.action.onClicked.addListener((tab) => {
  if (!tab?.id || !Number.isInteger(tab.windowId)) return
  void captureAssessmentTab(tab.id, tab.windowId)
    .then((capture) => storeAssessmentLaunch(tab, capture))
    .then((launchId) => openAssessmentAssistant(launchId))
    .then(() => Promise.allSettled([
      chrome.action.setBadgeText({ text: '', tabId: tab.id }),
      chrome.action.setTitle({ title: '识别当前线上笔试题目', tabId: tab.id })
    ]))
    .catch((error) => {
      const message = error instanceof Error ? error.message : '题面识别失败，请重试。'
      void Promise.allSettled([
        chrome.action.setBadgeBackgroundColor({ color: '#c2413b', tabId: tab.id }),
        chrome.action.setBadgeText({ text: '!', tabId: tab.id }),
        chrome.action.setTitle({ title: `题面识别失败：${message}`, tabId: tab.id })
      ])
    })
})
