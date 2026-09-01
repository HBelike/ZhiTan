const XHS_ORIGIN = 'https://www.xiaohongshu.com'
const NOTE_PATH_PATTERN = /\/(?:explore|search_result)\/([0-9a-z]{24})(?:[/?#]|$)/i

function text(value, maximum = 4000) {
  return typeof value === 'string' ? value.trim().slice(0, maximum) : ''
}

function noteIdFromUrl(value) {
  try {
    return new URL(value, XHS_ORIGIN).pathname.match(NOTE_PATH_PATTERN)?.[1] ?? ''
  } catch {
    return ''
  }
}

export function buildXiaohongshuSearchUrl(keyword) {
  const normalized = text(keyword, 180).replace(/\s+/g, ' ')
  if (!normalized) throw new Error('请输入小红书搜索关键词。')
  const url = new URL('/search_result', XHS_ORIGIN)
  url.searchParams.set('keyword', normalized)
  url.searchParams.set('source', 'web_search_result_notes')
  return url.toString()
}

export function buildXiaohongshuCanonicalUrl(noteId) {
  const normalized = text(noteId, 64)
  if (!/^[0-9a-z]{24}$/i.test(normalized)) throw new Error('小红书笔记标识不正确。')
  return `${XHS_ORIGIN}/explore/${normalized}`
}

export function classifyXiaohongshuPageState(snapshot = {}) {
  const url = text(snapshot.url, 2000).toLowerCase()
  const pageText = text(snapshot.pageText ?? snapshot.text, 12000).toLowerCase()
  let errorCode = ''
  try {
    errorCode = new URL(url).searchParams.get('error_code') || ''
  } catch {
    // URL 可能是页面尚未完成导航时的临时值，继续使用可见文本判断。
  }
  if (errorCode === '300031'
    || /当前笔记暂时无法浏览|请打开小红书app扫码查看/.test(pageText)) {
    return {
      code: 'content_restricted',
      message: '这篇笔记当前仅允许在小红书 App 查看，已跳过并继续处理下一篇。',
      stopBatch: false,
      retryable: false
    }
  }
  if (url.includes('/login') || /登录后|扫码登录|手机号登录|请先登录/.test(pageText)) {
    return {
      code: 'login_required',
      message: '请在已打开的小红书页面完成登录，然后回到面经库继续。',
      stopBatch: true,
      retryable: true
    }
  }
  if (/captcha|verify|security-check/.test(url)
    || /安全验证|完成验证|滑块验证|验证码|访问验证/.test(pageText)) {
    return {
      code: 'verification_required',
      message: '小红书要求安全验证，请在已打开的页面完成验证后继续。',
      stopBatch: true,
      retryable: true
    }
  }
  if (Number(snapshot.status) === 429 || /访问过于频繁|操作频繁|请求频繁|稍后再试/.test(pageText)) {
    return {
      code: 'rate_limited',
      message: '小红书限制了当前访问频率，采集已暂停，请稍后继续。',
      stopBatch: true,
      retryable: true
    }
  }
  return null
}

export function normalizeXiaohongshuSearchCards(rawCards, limit = 20) {
  const requestedLimit = Math.max(5, Math.min(50, Number(limit) || 20))
  const cards = []
  const seen = new Set()
  for (const raw of Array.isArray(rawCards) ? rawCards : []) {
    const signedUrl = text(raw?.signedUrl ?? raw?.url, 3000)
    const noteId = text(raw?.noteId, 64) || noteIdFromUrl(signedUrl)
    if (!/^[0-9a-z]{24}$/i.test(noteId) || seen.has(noteId)) continue
    let parsed
    try {
      parsed = new URL(signedUrl, XHS_ORIGIN)
    } catch {
      continue
    }
    if (parsed.protocol !== 'https:' || !parsed.hostname.endsWith('xiaohongshu.com')) continue
    seen.add(noteId)
    cards.push({
      noteId,
      signedUrl: parsed.toString(),
      canonicalUrl: buildXiaohongshuCanonicalUrl(noteId),
      title: text(raw?.title, 300) || '未命名小红书笔记',
      authorName: text(raw?.authorName, 120),
      likedCount: text(raw?.likedCount, 40),
      coverUrl: text(raw?.coverUrl, 3000)
    })
    if (cards.length >= requestedLimit) break
  }
  return cards
}

function normalizedImageUrls(values) {
  const result = []
  const seen = new Set()
  for (const value of Array.isArray(values) ? values : []) {
    try {
      const url = new URL(text(value, 5000))
      const host = url.hostname.toLowerCase()
      if (url.protocol !== 'https:'
        || !(host === 'xhscdn.com' || host.endsWith('.xhscdn.com') || host.endsWith('.xiaohongshu.com'))
        || seen.has(url.toString())) continue
      seen.add(url.toString())
      result.push(url.toString())
    } catch {
      // 页面可能混入头像、data URI 或无效资源，直接忽略。
    }
  }
  return result.slice(0, 20)
}

export function normalizeXiaohongshuNote(raw = {}, fallback = {}) {
  const noteId = text(raw.noteId, 64) || text(fallback.noteId, 64)
  const canonicalUrl = buildXiaohongshuCanonicalUrl(noteId)
  const tags = Array.from(new Set(
    (Array.isArray(raw.tags) ? raw.tags : [])
      .map((item) => text(typeof item === 'string' ? item : item?.name, 60).replace(/^#/, ''))
      .filter(Boolean)
  )).slice(0, 30)
  return {
    noteId,
    canonicalUrl,
    title: text(raw.title, 300) || text(fallback.title, 300) || '未命名小红书笔记',
    bodyText: text(raw.bodyText ?? raw.description, 100000),
    authorName: text(raw.authorName, 120) || text(fallback.authorName, 120),
    publishedAt: text(raw.publishedAt, 80),
    tags,
    imageUrls: normalizedImageUrls(raw.imageUrls)
  }
}
