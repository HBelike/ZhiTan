/*
 * 题面适配思路参考：
 * - RaheesAhmed/LeetCode-AI-Assistant（MIT）的问题、语言与样例提取
 * - harry-the-nerd/open-interview-assistant（Apache-2.0）的选择器回退
 * 本文件为本项目重新实现，不包含自动写入编辑器或自动提交逻辑。
 */

export const ASSESSMENT_CAPTURE_CAPABILITY = 'online_assessment_capture_v1'
export const ASSESSMENT_CAPTURE_MAX_TEXT = 25000

export function detectAssessmentPlatform(hostname = '') {
  const host = String(hostname).trim().toLowerCase()
  if (
    host === 'leetcode.com'
    || host.endsWith('.leetcode.com')
    || host === 'leetcode.cn'
    || host.endsWith('.leetcode.cn')
  ) return 'leetcode'
  if (host === 'hackerrank.com' || host.endsWith('.hackerrank.com')) return 'hackerrank'
  return 'generic'
}

export function extractFunctionSignature(starterCode = '') {
  const code = String(starterCode)
  const patterns = [
    /(?:^|\n)\s*(?:async\s+)?(def\s+[A-Za-z_$][\w$]*\s*\([^\n)]*\))/,
    /(?:^|\n)\s*((?:export\s+)?(?:async\s+)?function\s+[A-Za-z_$][\w$]*\s*\([^\n)]*\))/,
    /(?:^|\n)\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*\s*=\s*(?:async\s+)?function\s*\([^\n)]*\))/,
    /(?:^|\n)\s*((?:(?:public|private|protected|static|final|virtual)\s+)*[\w:<>,\[\] ?&*]+\s+[A-Za-z_$][\w$]*\s*\([^\n)]*\)(?:\s+const)?)/
  ]
  for (const pattern of patterns) {
    const match = code.match(pattern)
    if (match?.[1]) return match[1].trim()
  }
  return ''
}

export function normalizeAssessmentCapture(raw = {}) {
  const clean = (value, maximum = ASSESSMENT_CAPTURE_MAX_TEXT) => String(value ?? '')
    .replace(/\u0000/g, '')
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => line.replace(/[ \t]+/g, ' ').trim())
    .filter(Boolean)
    .join('\n')
    .slice(0, maximum)
  const candidates = Array.isArray(raw.problemCandidates)
    ? [...new Set(raw.problemCandidates.map((item) => clean(item)).filter(Boolean))].slice(0, 12)
    : []
  const publicTests = Array.isArray(raw.publicTestCandidates)
    ? raw.publicTestCandidates.slice(0, 20).map((item) => ({
        input: clean(item?.input, 12000),
        output: clean(item?.output, 12000),
        explanation: clean(item?.explanation, 1000)
      })).filter((item) => item.input && item.output)
    : []
  const viewport = raw.viewport && typeof raw.viewport === 'object' ? raw.viewport : {}
  return {
    sourceUrl: clean(raw.sourceUrl, 4000),
    sourceTitle: clean(raw.sourceTitle, 500),
    sourcePlatform: ['leetcode', 'hackerrank'].includes(raw.sourcePlatform) ? raw.sourcePlatform : 'generic',
    visibleText: clean(raw.visibleText),
    problemCandidates: candidates,
    starterCode: clean(raw.starterCode, 80000),
    languageHint: clean(raw.languageHint, 80).toLowerCase(),
    functionSignature: clean(raw.functionSignature, 2000),
    publicTestCandidates: publicTests,
    viewport: {
      width: Math.max(0, Math.round(Number(viewport.width) || 0)),
      height: Math.max(0, Math.round(Number(viewport.height) || 0)),
      devicePixelRatio: Math.max(0, Number(viewport.devicePixelRatio) || 1)
    },
    captureMode: clean(raw.captureMode, 80) || 'generic'
  }
}

export function extractAssessmentFromPage() {
  const MAX_TEXT = 25000
  // chrome.scripting.executeScript 只序列化当前函数，不能访问模块作用域中的导出函数。
  const detectPlatform = (hostname = '') => {
    const host = String(hostname).trim().toLowerCase()
    if (
      host === 'leetcode.com'
      || host.endsWith('.leetcode.com')
      || host === 'leetcode.cn'
      || host.endsWith('.leetcode.cn')
    ) return 'leetcode'
    if (host === 'hackerrank.com' || host.endsWith('.hackerrank.com')) return 'hackerrank'
    return 'generic'
  }
  const findFunctionSignature = (starterCode = '') => {
    const code = String(starterCode)
    const patterns = [
      /(?:^|\n)\s*(?:async\s+)?(def\s+[A-Za-z_$][\w$]*\s*\([^\n)]*\))/,
      /(?:^|\n)\s*((?:export\s+)?(?:async\s+)?function\s+[A-Za-z_$][\w$]*\s*\([^\n)]*\))/,
      /(?:^|\n)\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*\s*=\s*(?:async\s+)?function\s*\([^\n)]*\))/,
      /(?:^|\n)\s*((?:(?:public|private|protected|static|final|virtual)\s+)*[\w:<>,\[\] ?&*]+\s+[A-Za-z_$][\w$]*\s*\([^\n)]*\)(?:\s+const)?)/
    ]
    for (const pattern of patterns) {
      const match = code.match(pattern)
      if (match?.[1]) return match[1].trim()
    }
    return ''
  }
  const clean = (value, maximum = MAX_TEXT) => String(value ?? '')
    .replace(/\u0000/g, '')
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => line.replace(/[ \t]+/g, ' ').trim())
    .filter(Boolean)
    .join('\n')
    .slice(0, maximum)
  const isVisible = (element, viewportOnly = false) => {
    if (!element || !(element instanceof Element)) return false
    const style = getComputedStyle(element)
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false
    const rect = element.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) return false
    if (!viewportOnly) return true
    return rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth
  }
  const textFrom = (selector) => {
    for (const element of document.querySelectorAll(selector)) {
      if (!isVisible(element)) continue
      const text = clean(element.innerText || element.textContent)
      if (text) return text
    }
    return ''
  }
  const allTexts = (selectors) => [...new Set(selectors.flatMap((selector) =>
    [...document.querySelectorAll(selector)]
      .filter((element) => isVisible(element))
      .map((element) => clean(element.innerText || element.textContent))
      .filter(Boolean)
  ))]
  const visibleText = () => {
    const rejected = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'NAV', 'HEADER', 'FOOTER', 'FORM'])
    const texts = []
    for (const element of document.body?.querySelectorAll('main *, article *, section *, pre, p, li, h1, h2, h3') ?? []) {
      if (rejected.has(element.tagName) || !isVisible(element, true)) continue
      if (element.children.length > 3) continue
      const text = clean(element.innerText || element.textContent, 4000)
      if (text && !texts.includes(text)) texts.push(text)
      if (texts.join('\n').length >= MAX_TEXT) break
    }
    return clean(texts.join('\n'))
  }
  const codeText = () => {
    const editorSelectors = [
      '.monaco-editor .view-lines',
      '[data-mode-id] .view-lines',
      '.CodeMirror-code',
      '[data-testid*="code-editor"]',
      '[class*="code-editor"]'
    ]
    const editorText = textFrom(editorSelectors.join(', '))
    if (editorText) return clean(editorText, 80000)
    const textarea = [...document.querySelectorAll('textarea')]
      .filter(isVisible)
      .find((element) => /code|editor|代码/i.test(`${element.getAttribute('aria-label') || ''} ${element.className || ''}`))
    return clean(textarea?.value || '', 80000)
  }
  const languageText = () => {
    const selectors = [
      '[data-cy="lang-select"]',
      '[data-testid*="language"]',
      'button[id*="lang"]',
      'select[name*="language"]',
      '[class*="language"] button'
    ]
    for (const selector of selectors) {
      const element = [...document.querySelectorAll(selector)].find(isVisible)
      const value = element?.value || element?.textContent
      if (value) return clean(value, 80)
    }
    return ''
  }
  const publicTests = () => {
    const tests = []
    for (const pre of document.querySelectorAll('pre')) {
      if (!isVisible(pre)) continue
      const text = clean(pre.innerText || pre.textContent, 12000)
      const match = text.match(/(?:Input|输入)\s*:?\s*([\s\S]*?)(?:Output|输出)\s*:?\s*([\s\S]*?)(?:(?:Explanation|解释)\s*:?\s*([\s\S]*))?$/i)
      if (match?.[1] && match?.[2]) {
        tests.push({ input: clean(match[1], 12000), output: clean(match[2], 12000), explanation: clean(match[3], 1000) })
      }
      if (tests.length >= 20) break
    }
    return tests
  }
  const platform = detectPlatform(location.hostname)
  const isLeetCode = platform === 'leetcode'
  const isHackerRank = platform === 'hackerrank'
  const platformSelectors = isLeetCode
    ? ['[data-track-load="description_content"]', '[data-cy="question-content"]', '[class*="question-content"]']
    : isHackerRank
      ? ['.challenge-body-html', '[data-analytics="ChallengeDescription"]']
      : []
  const fallbackSelectors = [
    '[class*="question-description"]',
    '[class*="problem-statement"]',
    '#problem-statement',
    '[data-testid*="problem"]',
    'main',
    'article'
  ]
  const problemCandidates = allTexts([...platformSelectors, ...fallbackSelectors])
  const starterCode = codeText()
  return {
    sourceUrl: location.href,
    sourceTitle: document.title,
    sourcePlatform: platform,
    visibleText: visibleText(),
    problemCandidates,
    starterCode,
    languageHint: languageText(),
    functionSignature: findFunctionSignature(starterCode),
    publicTestCandidates: publicTests(),
    viewport: { width: innerWidth, height: innerHeight, devicePixelRatio },
    captureMode: platformSelectors.length && problemCandidates.length ? `${platform}_adapter` : problemCandidates.length ? 'generic_selector' : 'visible_viewport'
  }
}
