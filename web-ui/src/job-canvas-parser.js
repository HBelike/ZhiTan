export const JOB_SECTION_RULES = [
  { key: 'overview', label: '职位概览', headings: ['职位描述', '岗位介绍', '职位介绍', '岗位情况', '公司介绍'] },
  { key: 'highlights', label: '职位亮点', headings: ['职位亮点', '岗位亮点', '为什么加入', '岗位价值'] },
  { key: 'responsibilities', label: '岗位职责', headings: ['核心职责', '主要职责', '工作职责', '岗位职责', '职位职责', '你将负责', '工作内容'] },
  { key: 'requirements', label: '核心要求', headings: ['任职要求', '岗位要求', '职位要求', '基本要求', '核心要求', '任职资格', '岗位资格', '任职条件', '我们希望', '你需要'] },
  { key: 'bonus', label: '加分项', headings: ['加分项', '优先条件', '优先考虑', '加分条件', '加分要求', 'bonus'] },
  { key: 'benefits', label: '团队与福利', headings: ['职位福利', '福利待遇', '团队与福利', '我们提供', '你将获得', '工作地点'] }
]

const CANONICAL_TRAILING_HEADINGS = new Set(['技能关键词', '工作地点', '职位福利'])
const PAGE_CONTROL_PATTERN = /^(收藏|立即沟通|沟通|微信扫码分享|分享|举报|A举报)$/
const META_LINE_PATTERN = /^(公司|薪资|地点|城市|经验|学历)[：:]/

export function cleanJobLine(line) {
  return String(line ?? '')
    .replace(/<!--.*?-->/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/^\s{0,3}#{1,6}\s*/, '')
    .replace(/^[“”"']+\s*/, '')
    .replace(/[“”"']+$/, '')
    .replace(/^[\s\u200b\uFEFF]*\\?[-*+•·—–][\s\u200b]*/, '')
    .replace(/^\s*\d+[.)、]\s*/, '')
    .replace(/\*\*|__|`/g, '')
    .trim()
}

function normalizedHeading(line) {
  return cleanJobLine(line).replace(/^【|】$/g, '').replace(/[｜|]/g, '').replace(/[：:]$/, '').trim()
}

function sectionHeading(line) {
  const cleaned = cleanJobLine(line)
  for (const rule of JOB_SECTION_RULES) {
    for (const heading of rule.headings) {
      if (cleaned.toLowerCase() === heading.toLowerCase()) return { key: rule.key, content: '' }
      const match = cleaned.match(new RegExp(`^${heading.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[：:]\\s*(.*)$`, 'i'))
      if (match) return { key: rule.key, content: cleanJobLine(match[1]) }
    }
  }
  return null
}

function uniqueLines(lines) {
  const seen = new Set()
  return lines.filter((line) => {
    const key = line.replace(/\s+/g, '').toLowerCase()
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function listMarkerType(line) {
  const source = String(line ?? '')
    .replace(/<!--.*?-->/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/^\s{0,3}#{1,6}\s*/, '')
    .replace(/\*\*|__|`/g, '')
  if (/^\s*(?:\d+[.)、]|[一二三四五六七八九十]+[.)、])\s*/.test(source)) return 'number'
  if (/^\s*(?:[-*+•·▪◦—–])\s+/.test(source)) return 'bullet'
  return null
}

function mergeListContinuations(entries) {
  if (!entries.some((entry) => entry.marker)) return uniqueLines(entries.map((entry) => entry.text))
  const merged = []
  for (const entry of entries) {
    if (entry.marker || !merged.length) {
      merged.push(entry.text)
      continue
    }
    merged[merged.length - 1] = `${merged[merged.length - 1]}\n${entry.text}`
  }
  return uniqueLines(merged)
}

function extractsDisplayedSkills(requirements) {
  const skills = []
  const seen = new Set()
  for (const requirement of Array.isArray(requirements) ? requirements : []) {
    for (const value of Array.isArray(requirement?.skills) ? requirement.skills : []) {
      const skill = String(value ?? '').trim()
      const key = skill.toLowerCase()
      if (!skill || seen.has(key)) continue
      seen.add(key)
      skills.push(skill)
      if (skills.length >= 10) return skills
    }
  }
  return skills
}

function extractJobDescription(text) {
  const lines = String(text ?? '').replace(/\r/g, '').split('\n')
  const start = lines.findIndex((line) => normalizedHeading(line) === '职位描述')
  const source = start >= 0 ? lines.slice(start + 1) : lines
  const result = []
  for (const rawLine of source) {
    if (start >= 0 && CANONICAL_TRAILING_HEADINGS.has(normalizedHeading(rawLine))) break
    result.push(rawLine.replace(/^\s+|\s+$/g, ''))
  }
  while (result.length && !result[0]) result.shift()
  while (result.length && !result[result.length - 1]) result.pop()
  return result.join('\n').trim()
}

function isRedundantOtherLine(line, companyName, roleName, meta) {
  const compact = line.replace(/\s+/g, '').toLowerCase()
  if (!compact || PAGE_CONTROL_PATTERN.test(compact) || META_LINE_PATTERN.test(line)) return true
  if ([companyName, roleName].some((value) => String(value ?? '').replace(/\s+/g, '').toLowerCase() === compact)) return true
  return meta.some((item) => String(item.value ?? '').replace(/\s+/g, '').toLowerCase() === compact)
}

function detectMeta(text) {
  const compact = String(text ?? '').replace(/\s+/g, ' ')
  const salary = compact.match(/\d{1,3}(?:\.\d+)?\s*[-–—~至]\s*\d{1,3}(?:\.\d+)?\s*(?:[kK千]|万)(?:\s*[·x×]\s*\d+\s*薪)?|\d{1,3}\s*[kK]\s*以上/i)?.[0]
  const experience = compact.match(/经验不限|无需经验|\d+\s*[-–—~至]\s*\d+\s*年(?:以上)?(?:工作|研发|相关)?经验|(?:至少|不少于|具备)?\s*\d+\s*年(?:以上)?(?:工作|研发|相关)?经验/)?.[0]
  const education = compact.match(/学历不限|博士(?:及以上)?|硕士(?:及以上)?|本科(?:及以上|以上)?|大专(?:及以上|以上)?/)?.[0]
  const location = compact.match(/(?:工作地点|地点|城市)[：:]?\s*([\u4e00-\u9fa5]{2,10}(?:[、/·，,]\s*[\u4e00-\u9fa5]{2,10})*)/)?.[1] ?? compact.match(/(?:^|\s)(北京|上海|广州|深圳|杭州|南京|成都|武汉|苏州|西安|重庆|天津|长沙|厦门|合肥|青岛)(?=\s|$)/)?.[1]
  return [
    { label: '薪资范围', value: salary, emphasis: true },
    { label: '工作地点', value: location },
    { label: '经验要求', value: experience },
    { label: '学历要求', value: education }
  ].filter((item) => item.value)
}

export function isJobSubheadingLine(line) {
  return line.length <= 60 && (/^[^。！？!?]{2,56}[：:]$/.test(line) || /^【[^】]+】$/.test(line))
}

export function parseJobCanvasText(text, { companyName = '', roleName = '', requirements = [] } = {}) {
  const meta = detectMeta(text)
  const rawSections = Object.fromEntries([...JOB_SECTION_RULES.map((rule) => [rule.key, []]), ['other', []]])
  const detectedCoreSections = new Set()
  let currentSection = 'other'

  for (const rawLine of String(text ?? '').split(/\r?\n/)) {
    const line = cleanJobLine(rawLine)
    if (!line) continue
    if (normalizedHeading(rawLine) === '技能关键词') {
      currentSection = 'ignored'
      continue
    }
    const heading = sectionHeading(rawLine)
    if (heading) {
      currentSection = heading.key
      if (['responsibilities', 'requirements'].includes(heading.key)) detectedCoreSections.add(heading.key)
      if (heading.content) rawSections[currentSection].push({ text: heading.content, marker: null })
      continue
    }
    if (currentSection === 'ignored') continue
    if (PAGE_CONTROL_PATTERN.test(line.replace(/\s+/g, ''))) continue
    rawSections[currentSection].push({ text: line, marker: listMarkerType(rawLine) })
  }

  const listSections = new Set(['responsibilities', 'requirements', 'bonus', 'highlights', 'benefits'])
  const sections = Object.fromEntries(Object.entries(rawSections).map(([key, entries]) => [
    key,
    listSections.has(key) ? mergeListContinuations(entries) : uniqueLines(entries.map((entry) => entry.text))
  ]))
  sections.other = sections.other.filter((line) => !isRedundantOtherLine(line, companyName, roleName, meta))
  const hasResponsibilities = detectedCoreSections.has('responsibilities') && sections.responsibilities.length > 0
  const hasRequirements = detectedCoreSections.has('requirements') && sections.requirements.length > 0

  return {
    meta,
    skills: extractsDisplayedSkills(requirements),
    sections,
    other: sections.other,
    rawJobDescription: extractJobDescription(text),
    hasReliableStructure: hasResponsibilities && hasRequirements
  }
}
