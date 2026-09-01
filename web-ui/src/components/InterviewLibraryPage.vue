<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { jobLibraryBridge } from '../job-library-bridge.js'
import {
  createXiaohongshuDiscoveryPayload,
  createXiaohongshuNotePayload,
  hasXiaohongshuCollectionCapability,
  isXiaohongshuAccountBlockingError,
  normalizeXiaohongshuCollectionLimit,
  selectXiaohongshuCardsToProcess,
  XIAOHONGSHU_COLLECTION_JOB_STORAGE_KEY
} from '../xiaohongshu-collection.js'
import {
  normalizePublicWebCollectionLimit,
  normalizePublicWebSummary,
  publicWebAvailability,
  publicWebProgress,
  PUBLIC_WEB_COLLECTION_STAGES
} from '../public-web-collection.js'

const treeItems = ref([])
const treeLoading = ref(false)
const treeError = ref('')
const query = ref('')
const selectedExperience = ref(null)
const selectedExperienceId = ref('')
const detailLoading = ref(false)
const detailError = ref('')
const editMode = ref(false)
const saving = ref(false)
const deleting = ref(false)
const libraryNotice = ref('')
const editorCompanyName = ref('')
const editorRoleName = ref('')
const editorMarkdown = ref('')
const editorSummary = ref('')
const editorTags = ref('')
const showImportModal = ref(false)
const showCollectionModal = ref(false)
const importMode = ref('text')
const importing = ref(false)
const importProgress = ref(null)
const importError = ref('')
const importNotice = ref('')
const importFiles = ref([])
const fileProgressItems = ref([])
const fileInput = ref(null)
const fileParseResult = ref(null)
const parsedFileImports = ref([])
const fileImportStrategy = ref('merge')
const collectionMode = ref('xiaohongshu')
const collectionPlatforms = ref([])
const collectionLoading = ref(false)
const collectionSubmitting = ref(false)
const collectionError = ref('')
const collectionNotice = ref('')
const collectionJob = ref(null)
const collectionCandidates = ref([])
const browserCollectionRunning = ref(false)
const browserCollectionCards = ref([])
const candidateDrafts = ref({})
const collectionDraft = ref(createCollectionDraft())
let queryTimer = null
let collectionPollTimer = null
let browserCollectionStopRequested = false

const textDraft = ref(createTextDraft())
const fileDraft = ref(createFileDraft())

const emptyState = computed(() => !detailLoading.value && !selectedExperience.value)
const selectedTags = computed(() => selectedExperience.value?.tags ?? [])
const selectedSources = computed(() => selectedExperience.value?.sources ?? [])
const canWriteSelected = computed(() => selectedExperience.value?.can_write === true)
const renderedMarkdownBlocks = computed(() => parseMarkdownBlocks(selectedExperience.value?.markdown_content ?? ''))
const importFileCount = computed(() => importFiles.value.length)
const fileStrategyDescription = computed(() => fileImportStrategy.value === 'merge'
  ? `将 ${importFileCount.value || '所选'} 份材料归并为一份完整面经，适合同一场或同一岗位的多页资料。`
  : `将每份材料独立入库为一份面经，适合同一次导入多个不同岗位或公司的资料。`)
const collectionMetadata = computed(() => collectionJob.value?.metadata ?? {})
const publicWebSummary = computed(() => normalizePublicWebSummary(collectionMetadata.value.summary))
const publicWebCollectionProgress = computed(() => publicWebProgress(collectionMetadata.value, collectionJob.value?.status))
const publicWebAvailabilityState = computed(() => publicWebAvailability(collectionPlatforms.value))
const collectionAnalysisSummary = computed(() => {
  const metadata = collectionMetadata.value
  const declared = metadata.summary ?? metadata.statistics ?? metadata.stats ?? {}
  const candidates = collectionCandidates.value
  const imported = numberFrom(declared.imported_count, declared.imported, candidates.filter((item) => Boolean(candidateImportedExperienceId(item))).length)
  const rejected = numberFrom(
    declared.rejected_count,
    declared.invalid_count,
    candidates.filter((item) => candidateValidity(item) === false).length
  )
  const failed = numberFrom(
    declared.failed_count,
    declared.failed,
    candidates.filter((item) => ['failed', 'blocked', 'empty'].includes(item.status)).length
  )
  const valid = numberFrom(
    declared.valid_count,
    declared.valid,
    candidates.filter((item) => candidateValidity(item) === true).length
  )
  return {
    discovered: numberFrom(declared.discovered_count, declared.discovered, metadata.discovered_count, candidates.length),
    processed: numberFrom(declared.processed_count, declared.processed, metadata.processed_count, candidates.length),
    imported,
    rejected,
    failed,
    valid,
    incomplete: numberFrom(
      declared.incomplete_count,
      declared.insufficient_source_count,
      candidates.filter((item) => candidateAnalysisStatus(item) === 'insufficient_source').length
    )
  }
})
const collectionProgress = computed(() => {
  const metadata = collectionMetadata.value
  const status = String(collectionJob.value?.status ?? '').toLowerCase()
  const phase = String(metadata.phase ?? metadata.current_stage ?? metadata.stage ?? '').toLowerCase()
  const sequence = ['discover', 'fetch', 'ocr', 'analyze', 'import']
  const aliases = {
    discover: ['discover', 'discovering', 'finding', '链接发现'],
    fetch: ['fetch', 'reading', 'extract', '读取正文'],
    ocr: ['ocr', 'image', '图片识别'],
    analyze: ['analy', 'llm', 'classify', '甄别'],
    import: ['import', 'ingest', 'index', '写入']
  }
  const matched = sequence.findIndex((key) => aliases[key].some((token) => phase.includes(token)))
  const terminal = isCollectionTerminal(status)
  const currentIndex = terminal ? sequence.length - 1 : Math.max(0, matched)
  const declaredPercent = Number(metadata.progress_percent ?? metadata.percent ?? metadata.progress)
  return {
    currentIndex,
    percent: Number.isFinite(declaredPercent)
      ? Math.max(0, Math.min(100, declaredPercent))
      : terminal ? 100 : Math.max(8, (currentIndex / sequence.length) * 100),
    detail: metadata.progress_message ?? metadata.message ?? collectionJob.value?.error_message ?? ''
  }
})

function displayMarkdownText(value) {
  return value
    .replace(/!\[(.*?)\]\([^)]*\)/g, '$1')
    .replace(/\[(.*?)\]\([^)]*\)/g, '$1')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/__(.*?)__/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/~~(.*?)~~/g, '$1')
    .trim()
}

function parseMarkdownBlocks(markdown) {
  const blocks = []
  let paragraph = []
  let list = null

  const flushParagraph = () => {
    const text = displayMarkdownText(paragraph.join(' ').trim())
    if (text) blocks.push({ type: 'paragraph', text })
    paragraph = []
  }
  const flushList = () => {
    if (list?.items.length) blocks.push(list)
    list = null
  }

  for (const rawLine of markdown.replace(/\r\n/g, '\n').split('\n')) {
    const line = rawLine.trim()
    if (!line) {
      flushParagraph()
      flushList()
      continue
    }
    if (/^(---|\*\*\*|___)$/.test(line)) {
      flushParagraph()
      flushList()
      blocks.push({ type: 'divider' })
      continue
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      flushParagraph()
      flushList()
      blocks.push({ type: 'heading', level: heading[1].length, text: displayMarkdownText(heading[2]) })
      continue
    }
    const ordered = line.match(/^\d+[.)]\s+(.+)$/)
    const unordered = line.match(/^[-*+]\s+(.+)$/)
    if (ordered || unordered) {
      flushParagraph()
      const nextOrdered = Boolean(ordered)
      if (!list || list.ordered !== nextOrdered) {
        flushList()
        list = { type: 'list', ordered: nextOrdered, items: [] }
      }
      list.items.push(displayMarkdownText((ordered ?? unordered)[1]))
      continue
    }
    flushList()
    paragraph.push(line.replace(/^>\s?/, ''))
  }
  flushParagraph()
  flushList()
  return blocks.length ? blocks : [{ type: 'paragraph', text: '暂无可展示的面经正文。' }]
}

function createTextDraft() {
  return {
    companyName: '',
    roleName: '',
    interviewDate: '',
    sourcePlatform: '',
    sourceUrl: '',
    tags: '',
    summary: '',
    markdown: ''
  }
}

function createFileDraft() {
  return {
    companyName: '',
    roleName: '',
    interviewDate: '',
    sourcePlatform: '',
    sourceUrl: '',
    tags: '',
    summary: '',
    markdown: ''
  }
}

function normalizeParsedFileDraft(payload, manualDraft = createFileDraft()) {
  const parsedDraft = payload?.draft ?? {}
  return {
    companyName: manualDraft.companyName.trim() || parsedDraft.company_name || '',
    roleName: manualDraft.roleName.trim() || parsedDraft.role_name || '',
    interviewDate: manualDraft.interviewDate || parsedDraft.interview_date || '',
    sourcePlatform: manualDraft.sourcePlatform.trim() || parsedDraft.source_platform || '',
    sourceUrl: manualDraft.sourceUrl.trim() || parsedDraft.source_url || '',
    tags: manualDraft.tags.trim() || (parsedDraft.tags ?? []).join('，'),
    summary: manualDraft.summary.trim() || parsedDraft.summary_text || '',
    markdown: payload?.markdown_content ?? ''
  }
}

function buildFileExperiencePayload(draft) {
  return {
    company_name: draft.companyName.trim() || '待归档公司',
    role_name: draft.roleName.trim() || '未识别岗位',
    interview_date: draft.interviewDate || null,
    markdown_content: draft.markdown,
    source_type: 'manual_upload',
    source_platform: draft.sourcePlatform.trim() || null,
    source_url: draft.sourceUrl.trim() || null,
    summary_text: draft.summary.trim() || null,
    tags: parseTags(draft.tags)
  }
}

function mergeParsedFileImports(items) {
  const firstDraft = items[0]?.draft ?? createFileDraft()
  const tags = [...new Set(items.flatMap((item) => parseTags(item.draft.tags)))]
  const sections = items
    .map((item) => item.draft.markdown.trim())
    .filter(Boolean)
  return {
    ...firstDraft,
    tags: tags.join('，'),
    summary: firstDraft.summary || '',
    markdown: sections.join('\n\n')
  }
}

function createCollectionDraft() {
  return {
    platformKey: 'xiaohongshu',
    keyword: '',
    requestedLimit: 20,
    sourceUrl: '',
    includeImages: true,
    autoImport: false,
    companyName: '',
    roleName: '',
    interviewDate: '',
    summary: '',
    tags: ''
  }
}

function numberFrom(...values) {
  for (const value of values) {
    const number = Number(value)
    if (Number.isFinite(number) && number >= 0) return number
  }
  return 0
}

function getCandidateAnalysis(candidate) {
  const metadata = candidate?.metadata ?? {}
  return metadata.analysis ?? candidate?.analysis ?? {}
}

function candidateImportedExperienceId(candidate) {
  const metadata = candidate?.metadata ?? {}
  return candidate?.imported_experience_id ?? metadata.imported_experience_id ?? metadata.experience_id ?? null
}

function candidateValidity(candidate) {
  const analysis = getCandidateAnalysis(candidate)
  for (const key of ['is_valid_interview', 'is_valid', 'valid']) {
    if (typeof analysis[key] === 'boolean') return analysis[key]
    if (typeof analysis[key] === 'string') {
      const value = analysis[key].toLowerCase()
      if (['true', 'valid', 'yes'].includes(value)) return true
      if (['false', 'invalid', 'no'].includes(value)) return false
    }
  }
  if (['rejected', 'invalid', 'filtered'].includes(String(candidate?.status ?? '').toLowerCase())) return false
  return null
}

function candidateAnalysisStatus(candidate) {
  return String(getCandidateAnalysis(candidate).status ?? '').toLowerCase()
}

function candidateEvidence(candidate) {
  const images = candidate?.metadata?.images ?? candidate?.images ?? {}
  const evidence = images.evidence ?? candidate?.metadata?.evidence ?? {}
  return evidence && typeof evidence === 'object' ? evidence : {}
}

function candidateEvidenceText(candidate) {
  const evidence = candidateEvidence(candidate)
  const body = numberFrom(evidence.body_text_characters)
  const declared = numberFrom(evidence.image_declared_count)
  const downloaded = numberFrom(evidence.image_downloaded_count)
  const ocr = numberFrom(evidence.ocr_success_count)
  return `正文 ${body} 字 · 图片 ${declared} 张 · 已下载 ${downloaded} 张 · OCR 成功 ${ocr} 张`
}

function candidateIncompleteReason(candidate) {
  const analysis = getCandidateAnalysis(candidate)
  if (candidateAnalysisStatus(candidate) !== 'insufficient_source') return ''
  return analysis.message || '没有获得完整正文或配图文字，本条未作有效性判定，也不会自动入库。'
}

function candidateStatusText(candidate) {
  const processingState = String(candidate?.metadata?.processing_state ?? '').toLowerCase()
  if (processingState === 'duplicate') return '重复跳过'
  if (candidateImportedExperienceId(candidate)) return '已入库'
  if (candidateAnalysisStatus(candidate) === 'insufficient_source') return '采集不完整'
  const validity = candidateValidity(candidate)
  if (validity === true) return '有效面经'
  if (validity === false) return '已过滤'
  if (processingState === 'queued') return '等待解析'
  if (processingState === 'running') return '正在解析'
  const labels = {
    discovered: '待读取',
    fetched: '待甄别',
    selected: '待入库',
    imported: '已入库',
    failed: '解析失败',
    blocked: '访问受限',
    empty: '内容不足'
  }
  return labels[candidate?.status] ?? '处理中'
}

function collectionJobStatusText(status) {
  const labels = {
    queued: '等待开始',
    running: '正在处理',
    succeeded: '采集完成',
    completed: '采集完成',
    finished: '采集完成',
    failed: '任务失败',
    cancelled: '已取消',
    needs_user_interaction: '需要人工处理'
  }
  return labels[String(status ?? '').toLowerCase()] ?? String(status ?? '正在处理')
}

function candidateReason(candidate) {
  const analysis = getCandidateAnalysis(candidate)
  return analysis.rejection_reason
    ?? analysis.reason
    ?? candidate?.error_message
    ?? ''
}

function candidateCompany(candidate) {
  const analysis = getCandidateAnalysis(candidate)
  return analysis.company_name ?? analysis.company ?? ''
}

function candidateRole(candidate) {
  const analysis = getCandidateAnalysis(candidate)
  return analysis.role_name ?? analysis.role ?? analysis.job_name ?? ''
}

function createCandidateDraft(candidate) {
  const analysis = getCandidateAnalysis(candidate)
  return {
    markdownContent: String(candidate?.markdown_content ?? '').trim(),
    companyName: String(analysis.company_name ?? analysis.company ?? candidateCompany(candidate) ?? '待归档公司').trim() || '待归档公司',
    roleName: String(analysis.role_name ?? analysis.role ?? analysis.job_name ?? candidateRole(candidate) ?? '未识别岗位').trim() || '未识别岗位',
    interviewDate: String(analysis.interview_date ?? '').trim(),
    summary: String(analysis.summary_text ?? candidate?.excerpt ?? candidate?.snippet ?? candidate?.title ?? '').trim(),
    tags: Array.isArray(analysis.tags) ? analysis.tags.join('，') : String(analysis.tags ?? '').trim()
  }
}

function syncCandidateDrafts(candidates) {
  const next = {}
  for (const candidate of candidates ?? []) {
    const id = String(candidate.id)
    const suggested = createCandidateDraft(candidate)
    const current = candidateDrafts.value[id]
    next[id] = current
      ? {
          ...suggested,
          ...current,
          markdownContent: current.markdownContent || suggested.markdownContent,
          companyName: current.companyName || suggested.companyName,
          roleName: current.roleName || suggested.roleName,
          summary: current.summary || suggested.summary,
          tags: current.tags || suggested.tags
        }
      : suggested
  }
  candidateDrafts.value = next
}

function candidateDraft(candidate) {
  return candidateDrafts.value[String(candidate?.id)] ?? null
}

function canManuallySaveCandidate(candidate) {
  return Boolean(candidateDraft(candidate)?.markdownContent?.trim()) && !candidateImportedExperienceId(candidate)
}

function candidateConfidence(candidate) {
  const analysis = getCandidateAnalysis(candidate)
  const value = Number(analysis.confidence ?? analysis.score)
  if (!Number.isFinite(value)) return ''
  return `${Math.round(value <= 1 ? value * 100 : value)}%`
}

function isCollectionTerminal(status) {
  return ['succeeded', 'failed', 'cancelled', 'needs_user_interaction', 'completed', 'finished'].includes(String(status ?? '').toLowerCase())
}

function parseTags(value) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function formatUpdatedAt(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date)
}

function experienceSavedAt(experience) {
  return experience?.updated_at || experience?.created_at || ''
}

function statusText(value) {
  const labels = {
    queued: '等待处理',
    parsing: '解析中',
    parsed: '已解析',
    indexing: '建立索引中',
    indexed: '可检索',
    failed: '处理失败'
  }
  return labels[value] ?? value
}

function sourceText(value) {
  const labels = {
    manual_upload: '文件导入',
    manual_text: '手动录入',
    public_url: '公开链接',
    authenticated_session: '授权会话',
    official_api: '平台接口'
  }
  return labels[value] ?? value ?? '手动录入'
}

async function requestJson(path, options = {}) {
  let response
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...(options.headers ?? {})
      }
    })
  } catch (error) {
    // Vite 代理无法连接后端时，浏览器只会抛出没有上下文的 Failed to fetch。
    // 这里转成可操作的提示，避免用户误以为是上传文件本身损坏。
    if (error instanceof TypeError && /failed to fetch/i.test(error.message)) {
      throw new Error('无法连接开发后端（18080）。请运行 scripts\\start_dev_backend.ps1，并访问 http://127.0.0.1:18080/api/health 检查服务状态。')
    }
    throw error
  }

  const rawPayload = await response.text()
  let payload = {}
  try {
    payload = rawPayload ? JSON.parse(rawPayload) : {}
  } catch {
    // 代理层错误有时返回纯文本；保留正文，让界面能够展示真实故障而不是只显示状态码。
  }
  if (!response.ok) {
    const detail = typeof payload?.detail === 'string' ? payload.detail : rawPayload.trim()
    if (response.status === 404 && path.includes('/interview-library/parse-file')) {
      throw new Error('当前开发后端尚未加载面经文件解析接口。请使用项目启动脚本重启后端后重试。')
    }
    throw new Error(detail || `请求失败：${response.status}`)
  }
  return payload
}

function createFileProgressItems(files) {
  fileProgressItems.value = files.map((file, index) => ({
    index: index + 1,
    name: file.name,
    state: 'waiting',
    percent: 0,
    detail: '等待解析'
  }))
}

function updateFileParseProgress(context, event) {
  const percent = Math.max(0, Math.min(100, Number(event.percent) || 0))
  fileProgressItems.value = fileProgressItems.value.map((item) => (
    item.index === context.index
      ? { ...item, state: 'parsing', percent, detail: event.detail || event.phase || '正在解析' }
      : item
  ))
  const total = Math.max(1, context.total)
  importProgress.value = {
    percent: Math.round((((context.index - 1) + (percent / 100)) / total) * 100),
    phase: `正在解析第 ${context.index}/${total} 份材料`,
    detail: event.detail || event.phase || context.fileName,
    fileIndex: context.index,
    fileTotal: total,
    fileName: context.fileName
  }
}

function completeFileParse(context) {
  updateFileParseProgress(context, { percent: 100, phase: '解析完成', detail: `${context.fileName} 已完成` })
  fileProgressItems.value = fileProgressItems.value.map((item) => (
    item.index === context.index ? { ...item, state: 'completed', percent: 100, detail: '解析完成' } : item
  ))
}

function failFileParse(context, error) {
  const detail = error instanceof Error ? error.message : '解析失败'
  fileProgressItems.value = fileProgressItems.value.map((item) => (
    item.index === context.index ? { ...item, state: 'failed', detail } : item
  ))
}

function fileProgressStateText(item) {
  const labels = { waiting: '等待', parsing: `${item.percent}%`, completed: '已完成', failed: '失败' }
  return labels[item.state] ?? item.detail
}

async function requestFileParseWithProgress(data, context) {
  let response
  try {
    response = await fetch('/api/career/interview-library/parse-file-stream', {
      method: 'POST',
      body: data
    })
  } catch (error) {
    if (error instanceof TypeError && /failed to fetch/i.test(error.message)) {
      throw new Error('无法连接开发后端（18080）。请运行 scripts\\start_dev_backend.ps1，并访问 http://127.0.0.1:18080/api/health 检查服务状态。')
    }
    throw error
  }

  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `文件预解析请求失败：${response.status}`)
  }
  if (!response.body) {
    throw new Error('当前浏览器未返回解析进度流，请刷新页面后重试。')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let payload = null

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    let boundary = buffer.indexOf('\n')
    while (boundary >= 0) {
      const line = buffer.slice(0, boundary).trim()
      buffer = buffer.slice(boundary + 1)
      boundary = buffer.indexOf('\n')
      if (!line) continue
      let event
      try {
        event = JSON.parse(line)
      } catch {
        continue
      }
      if (event.event === 'progress') {
        updateFileParseProgress(context, event)
      } else if (event.event === 'result') {
        payload = event.payload
      } else if (event.event === 'error') {
        throw new Error(event.message || '文件解析未完成。')
      }
    }
    if (done) break
  }
  if (!payload) throw new Error('解析服务未返回可用草稿，请稍后重试。')
  return payload
}

async function loadTree({ preserveSelection = true } = {}) {
  treeLoading.value = true
  treeError.value = ''
  try {
    const params = new URLSearchParams()
    if (query.value.trim()) params.set('query', query.value.trim())
    const payload = await requestJson(`/api/career/interview-library/tree?${params.toString()}`)
    treeItems.value = payload.items ?? []

    if (preserveSelection && selectedExperienceId.value) {
      const stillExists = treeItems.value.some((company) =>
        company.children?.some((item) => item.id === selectedExperienceId.value)
      )
      if (!stillExists && query.value.trim()) {
        // 搜索过滤后保留当前正文，避免用户在编辑时意外丢失内容。
        return
      }
    }
  } catch (error) {
    treeError.value = error instanceof Error ? error.message : '面经树加载失败'
  } finally {
    treeLoading.value = false
  }
}

function queueTreeSearch() {
  if (queryTimer) window.clearTimeout(queryTimer)
  queryTimer = window.setTimeout(() => loadTree(), 220)
}

async function selectExperience(experienceId) {
  if (!experienceId || experienceId === selectedExperienceId.value) return
  if (editMode.value && !window.confirm('当前修改尚未保存，确定切换到另一份面经吗？')) return

  detailLoading.value = true
  detailError.value = ''
  libraryNotice.value = ''
  editMode.value = false
  try {
    const payload = await requestJson(`/api/career/interview-library/experiences/${encodeURIComponent(experienceId)}`)
    selectedExperience.value = payload
    selectedExperienceId.value = payload.id
    syncEditor(payload)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '面经正文加载失败'
  } finally {
    detailLoading.value = false
  }
}

function syncEditor(experience) {
  editorCompanyName.value = experience?.company_name ?? ''
  editorRoleName.value = experience?.role_name ?? ''
  editorMarkdown.value = experience?.markdown_content ?? ''
  editorSummary.value = experience?.summary_text ?? ''
  editorTags.value = (experience?.tags ?? []).join('，')
}

function startEditing() {
  if (!selectedExperience.value || !canWriteSelected.value) return
  syncEditor(selectedExperience.value)
  detailError.value = ''
  editMode.value = true
}

function cancelEditing() {
  syncEditor(selectedExperience.value)
  editMode.value = false
  detailError.value = ''
}

async function saveExperience() {
  if (!selectedExperience.value || !canWriteSelected.value) return
  if (!editorCompanyName.value.trim() || !editorRoleName.value.trim()) {
    detailError.value = '公司名称和面试岗位不能为空。'
    return
  }
  if (!editorMarkdown.value.trim()) {
    detailError.value = '面经正文不能为空。'
    return
  }

  saving.value = true
  detailError.value = ''
  try {
    const payload = await requestJson(
      `/api/career/interview-library/experiences/${encodeURIComponent(selectedExperience.value.id)}`,
      {
        method: 'PUT',
        body: JSON.stringify({
          company_name: editorCompanyName.value.trim(),
          role_name: editorRoleName.value.trim(),
          markdown_content: editorMarkdown.value,
          summary_text: editorSummary.value.trim() || null,
          tags: parseTags(editorTags.value)
        })
      }
    )
    selectedExperience.value = payload
    syncEditor(payload)
    editMode.value = false
    await loadTree()
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '保存失败，请稍后重试。'
  } finally {
    saving.value = false
  }
}

async function deleteExperience() {
  const experience = selectedExperience.value
  if (!experience || !canWriteSelected.value || deleting.value) return
  const label = `${experience.company_name} · ${experience.role_name}`
  if (!window.confirm(`永久删除“${label}”？正文、检索索引和来源记录都会被删除，且无法恢复。`)) return

  deleting.value = true
  detailError.value = ''
  libraryNotice.value = ''
  try {
    await requestJson(
      `/api/career/interview-library/experiences/${encodeURIComponent(experience.id)}`,
      { method: 'DELETE' }
    )
    selectedExperience.value = null
    selectedExperienceId.value = ''
    editMode.value = false
    await loadTree({ preserveSelection: false })
    libraryNotice.value = `面经已删除：${label}`
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '面经删除失败，请稍后重试。'
  } finally {
    deleting.value = false
  }
}

function openImport(mode = 'text') {
  importMode.value = mode
  importError.value = ''
  importNotice.value = ''
  if (mode === 'file') {
    fileParseResult.value = null
    parsedFileImports.value = []
  }
  showImportModal.value = true
}

function closeImport() {
  if (importing.value) return
  showImportModal.value = false
  importError.value = ''
  importNotice.value = ''
  fileParseResult.value = null
  parsedFileImports.value = []
}

function stopCollectionPolling() {
  if (collectionPollTimer) {
    window.clearTimeout(collectionPollTimer)
    collectionPollTimer = null
  }
}

async function refreshCollectionJob(jobId, { scheduleNext = true } = {}) {
  if (!jobId) return
  try {
    const payload = await requestJson(`/api/career/interview-library/collection-jobs/${encodeURIComponent(jobId)}`)
    const job = payload.job ?? payload
    if (collectionJob.value?.id && collectionJob.value.id !== job.id) return
    collectionJob.value = job
    collectionCandidates.value = payload.candidates ?? []
    syncCandidateDrafts(collectionCandidates.value)
    if (isCollectionTerminal(job.status)) {
      stopCollectionPolling()
      const summary = job.platform_key === 'public_web' ? publicWebSummary.value : collectionAnalysisSummary.value
      collectionNotice.value = job.status === 'succeeded' || job.status === 'completed'
        ? `采集完成：发现 ${summary.discovered} 条，识别有效面经 ${summary.valid} 条，已写入 ${summary.imported} 条。`
        : (job.error_message || '采集任务未完成，请查看失败条目的原因后重试。')
      if (summary.imported) await loadTree({ preserveSelection: false })
      return
    }
    if (scheduleNext && showCollectionModal.value) {
      stopCollectionPolling()
      collectionPollTimer = window.setTimeout(() => refreshCollectionJob(jobId), 1500)
    }
  } catch (error) {
    stopCollectionPolling()
    collectionError.value = error instanceof Error ? error.message : '采集进度读取失败，请稍后刷新重试。'
  }
}

function startCollectionPolling(jobId) {
  stopCollectionPolling()
  void refreshCollectionJob(jobId)
}

async function openCollection(mode = 'xiaohongshu') {
  stopCollectionPolling()
  collectionMode.value = mode
  collectionError.value = ''
  collectionNotice.value = ''
  collectionJob.value = null
  collectionCandidates.value = []
  candidateDrafts.value = {}
  if (mode === 'keyword') collectionDraft.value.requestedLimit = 10
  showCollectionModal.value = true
  if (collectionPlatforms.value.length || collectionLoading.value) return
  collectionLoading.value = true
  try {
    const payload = await requestJson('/api/career/interview-library/collection-platforms')
    collectionPlatforms.value = payload.items ?? []
    if (!collectionPlatforms.value.some((item) => item.key === collectionDraft.value.platformKey)) {
      collectionDraft.value.platformKey = collectionPlatforms.value[0]?.key ?? ''
    }
  } catch (error) {
    collectionError.value = error instanceof Error ? error.message : '采集平台信息加载失败。'
  } finally {
    collectionLoading.value = false
  }
}

function closeCollection() {
  if (collectionSubmitting.value) return
  stopCollectionPolling()
  showCollectionModal.value = false
  collectionError.value = ''
}

async function submitKeywordCollection() {
  const draft = collectionDraft.value
  if (!draft.keyword.trim()) {
    collectionError.value = '请输入公开网页搜索关键词。'
    return
  }
  if (!publicWebAvailabilityState.value.ready) {
    collectionError.value = publicWebAvailabilityState.value.reason
    return
  }
  collectionSubmitting.value = true
  collectionError.value = ''
  collectionNotice.value = ''
  collectionJob.value = null
  collectionCandidates.value = []
  candidateDrafts.value = {}
  stopCollectionPolling()
  try {
    const payload = await requestJson('/api/career/interview-library/public-web-imports', {
      method: 'POST',
      body: JSON.stringify({
        keyword: draft.keyword.trim(),
        requested_limit: normalizePublicWebCollectionLimit(draft.requestedLimit)
      })
    })
    const job = payload.job ?? payload
    if (!job?.id) throw new Error('公开网页收集服务未返回任务编号，请稍后重试。')
    collectionJob.value = job
    collectionCandidates.value = payload.candidates ?? []
    syncCandidateDrafts(collectionCandidates.value)
    collectionNotice.value = '任务已启动：正在搜索公开网页，已处理过的地址和重复正文会自动跳过。'
    startCollectionPolling(job.id)
  } catch (error) {
    collectionError.value = error instanceof Error ? error.message : '创建信息收集任务失败。'
  } finally {
    collectionSubmitting.value = false
  }
}

function browserCollectionDelay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

async function waitForBrowserCandidate(jobId, noteId, timeoutMs = 180000) {
  const startedAt = Date.now()
  while (!browserCollectionStopRequested && Date.now() - startedAt < timeoutMs) {
    const payload = await requestJson(`/api/career/interview-library/collection-jobs/${encodeURIComponent(jobId)}`)
    collectionJob.value = payload.job ?? payload
    collectionCandidates.value = payload.candidates ?? []
    syncCandidateDrafts(collectionCandidates.value)
    const candidate = collectionCandidates.value.find((item) => item?.metadata?.note_id === noteId)
    if (candidate && candidate.status !== 'discovered') return candidate
    await browserCollectionDelay(1200)
  }
  if (browserCollectionStopRequested) return null
  throw new Error('这篇笔记解析时间过长，任务已暂停，可稍后继续。')
}

async function pauseXiaohongshuBrowserCollection(error, { cancelled = false } = {}) {
  const jobId = collectionJob.value?.id
  if (!jobId) return
  const code = error?.code || (cancelled ? 'user_stopped' : 'browser_collection_paused')
  const message = error instanceof Error ? error.message : String(error?.message || (cancelled ? '用户已停止信息收集。' : '信息收集已暂停。'))
  try {
    const payload = await requestJson(`/api/career/interview-library/collection-jobs/${encodeURIComponent(jobId)}/pause`, {
      method: 'POST',
      body: JSON.stringify({ error_code: code, error_message: message, cancelled })
    })
    collectionJob.value = payload.job ?? payload
  } catch {
    // 保留原始浏览器错误，暂停状态写入失败不覆盖用户真正需要处理的问题。
  }
  if (cancelled) window.localStorage.removeItem(XIAOHONGSHU_COLLECTION_JOB_STORAGE_KEY)
}

async function runXiaohongshuBrowserCollection(job) {
  if (!job?.id || browserCollectionRunning.value) return
  browserCollectionRunning.value = true
  browserCollectionStopRequested = false
  collectionError.value = ''
  try {
    const connection = await jobLibraryBridge.ping()
    if (!hasXiaohongshuCollectionCapability(connection)) {
      throw new Error('当前浏览器助手版本不支持小红书信息收集，请更新助手后重试。')
    }
    const search = await jobLibraryBridge.searchXiaohongshuNotes(
      job.keyword || collectionDraft.value.keyword,
      normalizeXiaohongshuCollectionLimit(job.requested_limit || collectionDraft.value.requestedLimit)
    )
    browserCollectionCards.value = search.cards ?? []
    const discoveryPayload = createXiaohongshuDiscoveryPayload(browserCollectionCards.value)
    if (!discoveryPayload.items.length) throw new Error('没有读取到可处理的小红书笔记卡片。')
    const discovered = await requestJson(
      `/api/career/interview-library/collection-jobs/${encodeURIComponent(job.id)}/xiaohongshu-discoveries`,
      { method: 'POST', body: JSON.stringify(discoveryPayload) }
    )
    collectionCandidates.value = discovered.candidates ?? []
    syncCandidateDrafts(collectionCandidates.value)
    const cards = selectXiaohongshuCardsToProcess(browserCollectionCards.value, collectionCandidates.value)

    for (let index = 0; index < cards.length; index += 1) {
      if (browserCollectionStopRequested) break
      const card = cards[index]
      collectionNotice.value = `正在解析第 ${index + 1}/${cards.length} 篇：${card.title || '小红书笔记'}`
      let detail
      try {
        detail = await jobLibraryBridge.getXiaohongshuNote(card)
      } catch (error) {
        if (isXiaohongshuAccountBlockingError(error)) throw error
        detail = {
          noteId: card.noteId,
          title: card.title,
          authorName: card.authorName,
          bodyText: '',
          imageUrls: [],
          tags: [],
          sourceErrorCode: error?.code || 'note_read_failed',
          sourceErrorMessage: error instanceof Error ? error.message : '该笔记未能读取。'
        }
      }
      await requestJson(
        `/api/career/interview-library/collection-jobs/${encodeURIComponent(job.id)}/xiaohongshu-notes`,
        { method: 'POST', body: JSON.stringify(createXiaohongshuNotePayload(detail)) }
      )
      await waitForBrowserCandidate(job.id, card.noteId)
    }

    if (browserCollectionStopRequested) return
    const completed = await requestJson(
      `/api/career/interview-library/collection-jobs/${encodeURIComponent(job.id)}/complete`,
      { method: 'POST' }
    )
    collectionJob.value = completed.job ?? completed
    window.localStorage.removeItem(XIAOHONGSHU_COLLECTION_JOB_STORAGE_KEY)
    await refreshCollectionJob(job.id, { scheduleNext: false })
    await loadTree({ preserveSelection: false })
    const summary = collectionAnalysisSummary.value
    collectionNotice.value = `信息收集完成：发现 ${summary.discovered} 条，有效 ${summary.valid} 条，新入库 ${summary.imported} 条。`
  } catch (error) {
    await pauseXiaohongshuBrowserCollection(error)
    collectionError.value = error instanceof Error ? error.message : '信息收集已暂停，请检查小红书页面后继续。'
  } finally {
    browserCollectionRunning.value = false
  }
}

async function resumeKeywordCollection() {
  if (!collectionJob.value) return
  await runXiaohongshuBrowserCollection(collectionJob.value)
}

async function stopKeywordCollection() {
  browserCollectionStopRequested = true
  await pauseXiaohongshuBrowserCollection(new Error('用户已停止信息收集。'), { cancelled: true })
  browserCollectionRunning.value = false
  collectionNotice.value = '本次信息收集已停止；已完成的候选和面经不会删除。'
}

async function submitXiaohongshuCollection() {
  const draft = collectionDraft.value
  const sourceUrl = draft.sourceUrl.trim()
  if (!/^https:\/\//i.test(sourceUrl)) {
    collectionError.value = '请输入以 https:// 开头的小红书公开链接。'
    return
  }
  collectionSubmitting.value = true
  collectionError.value = ''
  collectionNotice.value = ''
  collectionJob.value = null
  collectionCandidates.value = []
  candidateDrafts.value = {}
  stopCollectionPolling()
  try {
    const payload = await requestJson('/api/career/interview-library/xiaohongshu-imports', {
      method: 'POST',
      body: JSON.stringify({
        source_url: sourceUrl,
        requested_limit: Math.max(1, Math.min(50, Number(draft.requestedLimit) || 10)),
        include_images: Boolean(draft.includeImages),
        auto_import: false
      })
    })
    const job = payload.job ?? payload
    if (!job?.id) throw new Error('采集服务未返回任务编号，请稍后重试。')
    collectionJob.value = job
    collectionCandidates.value = payload.candidates ?? []
    syncCandidateDrafts(collectionCandidates.value)
    collectionNotice.value = '采集任务已启动：将依次发现链接、读取正文、识别图片，并由 Agent 整理为可人工确认的候选正文。'
    startCollectionPolling(job.id)
  } catch (error) {
    collectionError.value = error instanceof Error ? error.message : '创建小红书采集任务失败。'
  } finally {
    collectionSubmitting.value = false
  }
}

async function submitUrlCollection() {
  const draft = collectionDraft.value
  if (!draft.sourceUrl.trim()) {
    collectionError.value = '请输入需要导入的公开 HTTPS 链接。'
    return
  }
  collectionSubmitting.value = true
  collectionError.value = ''
  collectionNotice.value = ''
  try {
    const payload = await requestJson('/api/career/interview-library/collect-url', {
      method: 'POST',
      body: JSON.stringify({ source_url: draft.sourceUrl.trim() })
    })
    collectionJob.value = payload.job
    collectionCandidates.value = [payload.candidate]
    syncCandidateDrafts(collectionCandidates.value)
    collectionNotice.value = '已读取公开页面并生成候选正文。确认信息后可写入面经库。'
  } catch (error) {
    collectionError.value = error instanceof Error ? error.message : '公开链接读取失败。'
  } finally {
    collectionSubmitting.value = false
  }
}

async function importCollectionCandidate(candidate) {
  const candidateReviewDraft = candidateDraft(candidate) ?? createCandidateDraft(candidate)
  const draft = collectionMode.value === 'url'
    ? {
        ...candidateReviewDraft,
        companyName: collectionDraft.value.companyName || candidateReviewDraft.companyName,
        roleName: collectionDraft.value.roleName || candidateReviewDraft.roleName,
        interviewDate: collectionDraft.value.interviewDate || candidateReviewDraft.interviewDate,
        summary: collectionDraft.value.summary || candidateReviewDraft.summary,
        tags: collectionDraft.value.tags || candidateReviewDraft.tags
      }
    : candidateReviewDraft
  const markdownContent = draft.markdownContent.trim()
  const companyName = draft.companyName.trim() || '待归档公司'
  const roleName = draft.roleName.trim() || '未识别岗位'
  if (!markdownContent) {
    collectionError.value = '请先确认候选正文，正文不能为空。'
    return
  }
  collectionSubmitting.value = true
  collectionError.value = ''
  try {
    const payload = await requestJson(
      `/api/career/interview-library/collection-candidates/${encodeURIComponent(candidate.id)}/import`,
      {
        method: 'POST',
        body: JSON.stringify({
          company_name: companyName,
          role_name: roleName,
          interview_date: draft.interviewDate || null,
          summary_text: draft.summary.trim() || candidate.title || null,
          tags: parseTags(draft.tags),
          markdown_content: markdownContent
        })
      }
    )
    await finishImport(payload)
    if (['xiaohongshu', 'keyword'].includes(collectionMode.value)) {
      collectionCandidates.value = collectionCandidates.value.map((item) => (
        item.id === candidate.id
          ? { ...item, imported_experience_id: payload.id, status: 'imported' }
          : item
      ))
      syncCandidateDrafts(collectionCandidates.value)
      collectionNotice.value = '已保存这条已核对的面经正文，并写入面经库。'
      await loadTree({ preserveSelection: false })
    } else {
      showCollectionModal.value = false
      collectionDraft.value = createCollectionDraft()
    }
  } catch (error) {
    collectionError.value = error instanceof Error ? error.message : '候选资料入库失败。'
  } finally {
    collectionSubmitting.value = false
  }
}

function handleFileChange(event) {
  importFiles.value = Array.from(event.target.files ?? [])
  createFileProgressItems(importFiles.value)
  fileParseResult.value = null
  parsedFileImports.value = []
  importError.value = ''
  importNotice.value = importFiles.value.length
    ? `已选择 ${importFiles.value.length} 份材料。${fileStrategyDescription.value}`
    : ''
}

async function submitTextImport() {
  const draft = textDraft.value
  if (!draft.companyName.trim() || !draft.roleName.trim() || !draft.markdown.trim()) {
    importError.value = '请填写公司、岗位和面经正文。'
    return
  }
  importing.value = true
  importError.value = ''
  try {
    const payload = await requestJson('/api/career/interview-library/experiences', {
      method: 'POST',
      body: JSON.stringify({
        company_name: draft.companyName,
        role_name: draft.roleName,
        interview_date: draft.interviewDate || null,
        markdown_content: draft.markdown,
        source_type: draft.sourceUrl.trim() ? 'public_url' : 'manual_text',
        source_platform: draft.sourcePlatform.trim() || null,
        source_url: draft.sourceUrl.trim() || null,
        summary_text: draft.summary.trim() || null,
        tags: parseTags(draft.tags)
      })
    })
    await finishImport(payload)
    textDraft.value = createTextDraft()
  } catch (error) {
    importError.value = error instanceof Error ? error.message : '面经入库失败。'
  } finally {
    importing.value = false
  }
}

async function parseFileImport() {
  const manualDraft = { ...fileDraft.value }
  if (!importFiles.value.length) {
    importError.value = '请先选择至少一份文件。公司、岗位等信息可以由系统自动识别，也可以在解析后手动修正。'
    return
  }
  importing.value = true
  if (fileProgressItems.value.length !== importFiles.value.length) {
    createFileProgressItems(importFiles.value)
  }
  importProgress.value = {
    percent: 3,
    phase: '正在准备解析',
    detail: '正在传输文件，并准备提取面经正文。',
    fileIndex: 1,
    fileTotal: importFiles.value.length,
    fileName: importFiles.value[0]?.name ?? ''
  }
  importError.value = ''
  try {
    const parsedItems = []
    for (let index = 0; index < importFiles.value.length; index += 1) {
      const file = importFiles.value[index]
      const context = { index: index + 1, total: importFiles.value.length, fileName: file.name }
      updateFileParseProgress(context, { percent: 0, phase: '准备解析', detail: `${file.name} 正在排队` })
      const data = new FormData()
      if (manualDraft.sourcePlatform.trim()) data.set('source_platform', manualDraft.sourcePlatform)
      if (manualDraft.sourceUrl.trim()) data.set('source_url', manualDraft.sourceUrl)
      data.set('source_file', file)
      let payload
      try {
        payload = await requestFileParseWithProgress(data, context)
        completeFileParse(context)
      } catch (error) {
        failFileParse(context, error)
        throw error
      }
      parsedItems.push({
        fileName: file.name,
        draft: normalizeParsedFileDraft(payload, manualDraft),
        recognition: payload.recognition ?? {}
      })
    }
    parsedFileImports.value = parsedItems
    if (fileImportStrategy.value === 'merge') {
      fileDraft.value = mergeParsedFileImports(parsedItems)
    } else {
      fileDraft.value = { ...parsedItems[0].draft }
    }
    fileParseResult.value = {
      confidence: parsedItems.reduce((total, item) => total + Number(item.recognition.confidence ?? 0), 0) / parsedItems.length,
      evidence: parsedItems.flatMap((item) => item.recognition.evidence ?? []).slice(0, 8),
      warnings: parsedItems.flatMap((item) => item.recognition.warnings ?? [])
    }
    importNotice.value = fileImportStrategy.value === 'merge'
      ? `已完成 ${parsedItems.length} 份材料的识别并归并为一份面经。请核对字段和正文后保存。`
      : `已完成 ${parsedItems.length} 份材料的识别。保存后会生成 ${parsedItems.length} 份独立面经；当前展示第一份内容供核对。`
  } catch (error) {
    importError.value = error instanceof Error ? error.message : '文件解析失败。'
  } finally {
    if (importProgress.value?.percent === 100) {
      await new Promise((resolve) => window.setTimeout(resolve, 240))
    }
    importing.value = false
    importProgress.value = null
  }
}

async function saveParsedFileImport() {
  if (!fileParseResult.value || !parsedFileImports.value.length || !fileDraft.value.markdown.trim()) {
    importError.value = '请先完成文件解析，并确认已生成可用的面经正文。'
    return
  }
  importing.value = true
  importError.value = ''
  try {
    const drafts = fileImportStrategy.value === 'merge'
      ? [fileDraft.value]
      : parsedFileImports.value.map((item) => item.draft)
    const imported = []
    for (const draft of drafts) {
      const payload = await requestJson('/api/career/interview-library/experiences', {
        method: 'POST',
        body: JSON.stringify(buildFileExperiencePayload(draft))
      })
      imported.push(payload)
    }
    await finishImport(imported[imported.length - 1], { importedCount: imported.length })
    fileDraft.value = createFileDraft()
    fileParseResult.value = null
    parsedFileImports.value = []
    importFiles.value = []
    if (fileInput.value) fileInput.value.value = ''
  } catch (error) {
    importError.value = error instanceof Error ? error.message : '面经入库失败。'
  } finally {
    importing.value = false
  }
}

async function finishImport(payload, { importedCount = 1 } = {}) {
  importNotice.value = importedCount > 1
    ? `已分别入库 ${importedCount} 份面经，并建立检索索引。`
    : `已入库并建立 ${payload.status === 'indexed' ? '检索索引' : '解析记录'}：${payload.role_name}`
  await loadTree({ preserveSelection: false })
  selectedExperience.value = payload
  selectedExperienceId.value = payload.id
  syncEditor(payload)
  window.setTimeout(() => {
    showImportModal.value = false
    importNotice.value = ''
  }, 700)
}

onMounted(async () => {
  await loadTree({ preserveSelection: false })
  const linkedExperienceId = new URLSearchParams(window.location.search).get('experience_id')
  if (linkedExperienceId) await selectExperience(linkedExperienceId)
})

onBeforeUnmount(() => {
  if (queryTimer) window.clearTimeout(queryTimer)
  stopCollectionPolling()
  if (browserCollectionRunning.value && collectionJob.value?.id) {
    browserCollectionStopRequested = true
    void window.fetch(
      `/api/career/interview-library/collection-jobs/${encodeURIComponent(collectionJob.value.id)}/pause`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          error_code: 'browser_page_closed',
          error_message: '面经库页面已关闭，未读取的笔记将在下次打开后继续。',
          cancelled: false
        }),
        keepalive: true
      }
    )
  }
})
</script>

<template>
  <section class="interview-library-shell">
    <header class="library-header">
      <div class="library-heading">
        <span class="library-heading-mark" aria-hidden="true">↥</span>
        <div>
          <strong>导入与收录</strong>
          <p class="library-description">把零散经历沉淀成可检索、可追溯的面试证据</p>
        </div>
      </div>
      <div class="library-actions">
        <button type="button" class="quiet-action collection-action" @click="openCollection('xiaohongshu')"><span aria-hidden="true">⌁</span>小红书URL读取</button>
        <button type="button" class="quiet-action information-collection-action" @click="openCollection('keyword')"><span aria-hidden="true">⌕</span>公开信息收集</button>
        <button type="button" class="primary-action upload-action" @click="openImport('file')"><span aria-hidden="true">↑</span>上传面经</button>
      </div>
    </header>

    <p v-if="libraryNotice" class="library-notice" role="status">{{ libraryNotice }}</p>

    <section class="library-layout">
      <aside class="interview-tree-pane" aria-label="面经树">
        <div class="tree-pane-header">
          <div>
            <p class="section-label">面经树</p>
            <strong>{{ treeItems.length }} 家公司</strong>
          </div>
          <button type="button" class="tree-refresh" :disabled="treeLoading" title="刷新面经树" @click="loadTree()">↻</button>
        </div>

        <label class="tree-search">
          <span>⌕</span>
          <input v-model="query" type="search" placeholder="搜索公司或岗位" @input="queueTreeSearch" />
        </label>

        <p v-if="treeError" class="inline-error">{{ treeError }}</p>
        <div v-else-if="treeLoading" class="tree-placeholder">正在整理面经树…</div>
        <div v-else-if="!treeItems.length" class="tree-placeholder">
          <strong>还没有面经</strong>
          <span>从顶部导入一份材料，系统会自动按公司和岗位归档。</span>
        </div>
        <nav v-else class="company-tree">
          <details v-for="company in treeItems" :key="company.id" open>
            <summary>
              <span class="tree-company-mark">◫</span>
              <span>{{ company.label }}</span>
              <em>{{ company.children?.length ?? 0 }}</em>
            </summary>
            <div class="tree-leaves">
              <button
                v-for="experience in company.children"
                :key="experience.id"
                type="button"
                class="tree-leaf"
                :class="{ active: selectedExperienceId === experience.id }"
                @click="selectExperience(experience.id)"
              >
                <span class="leaf-line"></span>
                <span class="leaf-copy">
                  <strong>{{ experience.role_name }}</strong>
                  <small>更新于 {{ formatUpdatedAt(experienceSavedAt(experience)) }}</small>
                </span>
                <i :title="statusText(experience.status)" :class="`status-dot status-${experience.status}`"></i>
              </button>
            </div>
          </details>
        </nav>
      </aside>

      <article class="interview-detail-pane">
        <div v-if="detailLoading" class="detail-state">
          <span class="thinking-orbit"></span>
          <strong>正在读取面经正文</strong>
          <p>加载来源、结构化 Markdown 与检索状态。</p>
        </div>

        <div v-else-if="detailError" class="detail-state error-state">
          <strong>正文暂时无法打开</strong>
          <p>{{ detailError }}</p>
          <button type="button" class="quiet-action" @click="selectedExperienceId && selectExperience(selectedExperienceId)">重试</button>
        </div>

        <div v-else-if="emptyState" class="detail-state library-empty-state">
          <div class="empty-index-mark">@</div>
          <p class="section-label">RETRIEVAL READY</p>
          <h3>从一份真实面经开始</h3>
          <p>导入 PDF、Word、Excel、图片或整理好的 Markdown。平台只持久化解析后的文本、来源与索引，不保留上传原件。</p>
          <button type="button" class="primary-action" @click="openImport('file')">导入第一份材料</button>
        </div>

        <template v-else>
          <header class="experience-header">
            <div>
              <p class="section-label">{{ selectedExperience.company_name }}</p>
              <h3>{{ selectedExperience.role_name }}</h3>
              <div class="experience-meta">
                <span>更新于 {{ formatUpdatedAt(experienceSavedAt(selectedExperience)) }}</span>
                <span>{{ sourceText(selectedExperience.source_type) }}</span>
                <a v-if="selectedExperience.source_url" :href="selectedExperience.source_url" target="_blank" rel="noreferrer">查看来源 ↗</a>
              </div>
            </div>
            <div class="experience-actions">
              <span class="index-status" :class="`index-${selectedExperience.status}`">{{ statusText(selectedExperience.status) }}</span>
              <template v-if="!editMode && canWriteSelected">
                <button type="button" class="danger-action" :disabled="deleting" @click="deleteExperience">{{ deleting ? '正在删除…' : '删除面经' }}</button>
                <button type="button" class="quiet-action" :disabled="deleting" @click="startEditing">编辑面经</button>
              </template>
              <span v-else-if="!editMode" class="read-only-notice">公开面经，仅创建者或管理员可维护</span>
              <template v-else>
                <button type="button" class="quiet-action" :disabled="saving" @click="cancelEditing">取消</button>
                <button type="button" class="primary-action" :disabled="saving" @click="saveExperience">
                  {{ saving ? '保存并重建索引…' : '保存并更新' }}
                </button>
              </template>
            </div>
          </header>

          <section class="experience-context-strip">
            <span>来源 · {{ selectedExperience.source_platform || sourceText(selectedExperience.source_type) }}</span>
            <div class="tag-list">
              <span v-for="tag in selectedTags" :key="tag"># {{ tag }}</span>
            </div>
          </section>

          <details v-if="selectedSources.length" class="experience-source-ledger">
            <summary>来源记录 <span>{{ selectedSources.length }} 个公开地址</span></summary>
            <div class="experience-source-list">
              <a
                v-for="source in selectedSources"
                :key="source.id"
                :href="source.source_url"
                target="_blank"
                rel="noreferrer"
              >
                <strong>{{ source.source_platform }}{{ source.is_primary ? ' · 主来源' : ' · 同正文来源' }}</strong>
                <span>{{ source.canonical_url }}</span>
                <small v-if="source.discovery_keywords?.length">检索词：{{ source.discovery_keywords.join('、') }}</small>
              </a>
            </div>
          </details>

          <div v-if="editMode" class="editor-layout">
            <section class="editor-identity-panel" aria-label="面经归属信息">
              <label class="editor-field">
                <span>公司名称</span>
                <input
                  v-model="editorCompanyName"
                  type="text"
                  maxlength="120"
                  autocomplete="organization"
                  placeholder="例如：小红书"
                />
              </label>
              <label class="editor-field">
                <span>面试岗位</span>
                <input
                  v-model="editorRoleName"
                  type="text"
                  maxlength="160"
                  placeholder="例如：AI Agent 开发"
                />
              </label>
            </section>
            <label class="editor-field markdown-field editor-canvas">
              <span>面经正文 <em>{{ editorMarkdown.trim().length }} 字</em></span>
              <textarea v-model="editorMarkdown" spellcheck="false" aria-label="编辑面经 Markdown"></textarea>
            </label>
            <details class="editor-support-panel">
              <summary>补充信息 <span>摘要与标签</span></summary>
              <div class="editor-support-fields">
                <label class="editor-field summary-field">
                  <span>摘要</span>
                  <input v-model="editorSummary" type="text" placeholder="一句话说明这份面经的价值" />
                </label>
                <label class="editor-field tag-field">
                  <span>标签</span>
                  <input v-model="editorTags" type="text" placeholder="例如：一面，Java，系统设计（用逗号分隔）" />
                </label>
              </div>
            </details>
          </div>
          <section v-else class="reading-layout">
            <aside v-if="selectedExperience.summary_text" class="summary-note">
              <p>核心摘要</p>
              <strong>{{ selectedExperience.summary_text }}</strong>
            </aside>
            <article class="markdown-reading" aria-label="面经正文阅读器">
              <template v-for="(block, index) in renderedMarkdownBlocks" :key="`${block.type}-${index}`">
                <hr v-if="block.type === 'divider'" class="reading-divider" />
                <component
                  :is="`h${Math.min(block.level + 1, 4)}`"
                  v-else-if="block.type === 'heading'"
                  class="reading-heading"
                >{{ block.text }}</component>
                <ol v-else-if="block.type === 'list' && block.ordered" class="reading-list reading-ordered">
                  <li v-for="(item, itemIndex) in block.items" :key="itemIndex">{{ item }}</li>
                </ol>
                <ul v-else-if="block.type === 'list'" class="reading-list">
                  <li v-for="(item, itemIndex) in block.items" :key="itemIndex">{{ item }}</li>
                </ul>
                <p v-else class="reading-paragraph">{{ block.text }}</p>
              </template>
            </article>
          </section>
        </template>
      </article>
    </section>

    <Teleport to="body">
      <div v-if="showImportModal" class="import-backdrop" role="presentation" @click.self="closeImport">
        <section class="import-dialog" role="dialog" aria-modal="true" aria-labelledby="importTitle">
          <div v-if="importing && importMode === 'file' && !fileParseResult" class="file-parse-overlay" role="status" aria-live="polite">
            <div class="file-parse-progress-card">
              <div class="progress-heading"><span>材料解析中</span><strong>{{ importProgress?.percent ?? 0 }}%</strong></div>
              <div class="progress-track" aria-hidden="true"><i :style="{ width: `${importProgress?.percent ?? 0}%` }"></i></div>
              <strong class="progress-phase">{{ importProgress?.phase || '正在准备解析' }}</strong>
              <p>{{ importProgress?.detail || '系统会自动识别正文、公司、岗位与面试线索。' }}</p>
              <div class="file-progress-context">
                <span>第 {{ importProgress?.fileIndex ?? 1 }} / {{ importProgress?.fileTotal ?? importFileCount }} 份</span>
                <strong>{{ importProgress?.fileName || '正在准备材料' }}</strong>
              </div>
              <ol v-if="fileProgressItems.length" class="file-progress-list">
                <li v-for="item in fileProgressItems" :key="`${item.index}-${item.name}`" :class="`is-${item.state}`">
                  <span>{{ item.index }}</span>
                  <strong>{{ item.name }}</strong>
                  <em>{{ fileProgressStateText(item) }}</em>
                </li>
              </ol>
            </div>
          </div>
          <header>
            <div>
              <p class="library-kicker">NEW INTERVIEW EVIDENCE</p>
              <h2 id="importTitle">导入面经材料</h2>
              <p>上传原件只在解析任务中暂存；入库后仅保留 Markdown、来源信息与检索切片。</p>
            </div>
            <button type="button" class="close-button" :disabled="importing" aria-label="关闭" @click="closeImport">×</button>
          </header>

          <nav class="import-tabs" aria-label="导入方式">
            <button type="button" :class="{ active: importMode === 'text' }" @click="importMode = 'text'">粘贴正文</button>
            <button type="button" :class="{ active: importMode === 'file' }" @click="importMode = 'file'">上传文件</button>
          </nav>

          <p v-if="importError" class="dialog-error">{{ importError }}</p>
          <p v-if="importNotice" class="dialog-success">{{ importNotice }}</p>

          <form v-if="importMode === 'text'" class="import-form" @submit.prevent="submitTextImport">
            <div class="form-grid">
              <label>公司名称<input v-model="textDraft.companyName" required placeholder="例如：字节跳动" /></label>
              <label>面试岗位<input v-model="textDraft.roleName" required placeholder="例如：Java 后端开发工程师" /></label>
              <label>面试日期<input v-model="textDraft.interviewDate" type="date" /></label>
              <label>来源平台<input v-model="textDraft.sourcePlatform" placeholder="例如：牛客、小红书" /></label>
            </div>
            <label>来源 URL（可选）<input v-model="textDraft.sourceUrl" type="url" placeholder="https://…" /></label>
            <label>标签（可选）<input v-model="textDraft.tags" placeholder="一面，Java，系统设计" /></label>
            <label>摘要（可选）<input v-model="textDraft.summary" placeholder="这份面经适合解决什么问题？" /></label>
            <label>面经正文 Markdown<textarea v-model="textDraft.markdown" required placeholder="# 面试流程\n\n## 一面\n- …"></textarea></label>
            <footer><button type="button" class="quiet-action" :disabled="importing" @click="closeImport">取消</button><button class="primary-action" :disabled="importing">{{ importing ? '正在入库…' : '保存并建立索引' }}</button></footer>
          </form>

          <form v-else class="import-form" @submit.prevent="fileParseResult ? saveParsedFileImport() : parseFileImport()">
            <section class="file-intake-intro">
              <strong>先上传，系统自动预填</strong>
              <p>系统会解析公司、岗位、轮次、技术标签和面经正文；下方字段只用于核对与修正，不要求你先手工填写。</p>
            </section>
            <label class="file-picker">选择材料<input ref="fileInput" type="file" multiple accept=".pdf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png,.webp" @change="handleFileChange" /><span>{{ importFileCount ? `已选择 ${importFileCount} 份材料` : '支持多选：PDF、Word、Excel、JPG、PNG、WebP' }}</span></label>
            <fieldset class="file-import-strategy">
              <legend>归档方式</legend>
              <label><input v-model="fileImportStrategy" type="radio" value="merge" :disabled="Boolean(fileParseResult)" />多文件归并为一份面经</label>
              <label><input v-model="fileImportStrategy" type="radio" value="separate" :disabled="Boolean(fileParseResult)" />每个文件独立生成面经</label>
              <p>{{ fileStrategyDescription }}</p>
            </fieldset>
            <section v-if="fileParseResult" class="recognition-card">
              <div><strong>自动识别完成</strong><span>置信度 {{ Math.round((fileParseResult.confidence ?? 0) * 100) }}%</span></div>
              <p v-if="fileParseResult.evidence?.length">{{ fileParseResult.evidence.join(' · ') }}</p>
              <p v-for="warning in fileParseResult.warnings ?? []" :key="warning" class="recognition-warning">{{ warning }}</p>
            </section>
            <div class="form-grid">
              <label>公司名称（可修正）<input v-model="fileDraft.companyName" placeholder="识别失败时可手工补充，例如：字节跳动" /></label>
              <label>面试岗位（可修正）<input v-model="fileDraft.roleName" placeholder="识别失败时可手工补充，例如：AI Agent 开发" /></label>
              <label>面试日期<input v-model="fileDraft.interviewDate" type="date" /></label>
              <label>来源平台<input v-model="fileDraft.sourcePlatform" placeholder="例如：牛客、小红书" /></label>
            </div>
            <label>来源 URL（可选）<input v-model="fileDraft.sourceUrl" type="url" placeholder="https://…" /></label>
            <label>标签（可选）<input v-model="fileDraft.tags" placeholder="一面，Java，系统设计" /></label>
            <label>摘要（可选）<input v-model="fileDraft.summary" placeholder="这份材料的来源或特点" /></label>
            <label v-if="fileParseResult" class="markdown-field">{{ fileImportStrategy === 'merge' ? '归并后的面经正文（可编辑）' : '第一份面经正文（可编辑）' }}<textarea v-model="fileDraft.markdown" required placeholder="解析后的 Markdown 正文会显示在这里"></textarea></label>
            <footer><button type="button" class="quiet-action" :disabled="importing" @click="closeImport">取消</button><button class="primary-action" :disabled="importing">{{ importing ? (fileParseResult ? '正在建立索引…' : '正在解析…') : (fileParseResult ? '确认保存并建立索引' : '解析并预填') }}</button></footer>
          </form>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="showCollectionModal" class="collection-backdrop" role="presentation" @click.self="closeCollection">
        <section class="collection-dialog" role="dialog" aria-modal="true" aria-labelledby="collectionTitle">
          <header class="collection-dialog-header">
            <div>
              <h2 id="collectionTitle">{{ collectionMode === 'keyword' ? '全网公开信息收集' : '导入公开面经资料' }}</h2>
              <p v-if="collectionMode === 'keyword'">输入检索词后，系统会从无需登录的公开网页发现面经，逐页提取正文并自动去重。图片只用于临时 OCR，不保存图片；有效内容自动写入面经库。</p>
              <p v-else>粘贴公开笔记或网页链接，系统会读取可访问的正文与图片文字并整理为可编辑候选；确认保存后写入面经库。</p>
            </div>
            <button type="button" class="close-button" :disabled="collectionSubmitting" aria-label="关闭" @click="closeCollection">×</button>
          </header>

          <nav class="import-tabs collection-tabs" aria-label="采集方式">
            <button type="button" :class="{ active: collectionMode === 'xiaohongshu' }" @click="collectionMode = 'xiaohongshu'">小红书链接导入</button>
            <button type="button" :class="{ active: collectionMode === 'url' }" @click="collectionMode = 'url'">单篇公开链接</button>
            <button type="button" :class="{ active: collectionMode === 'keyword' }" @click="collectionMode = 'keyword'">全网公开信息收集</button>
          </nav>

          <p v-if="collectionError" class="dialog-error">{{ collectionError }}</p>
          <p v-if="collectionNotice" class="dialog-success">{{ collectionNotice }}</p>

          <form v-if="collectionMode === 'xiaohongshu'" class="collection-form xiaohongshu-import-form" @submit.prevent="submitXiaohongshuCollection">
            <section class="collection-section xiaohongshu-source-section">
              <div class="collection-section-heading">
                <div>
                  <h3>粘贴小红书链接</h3>
                  <p>可提交公开笔记、用户主页、收藏页和搜索结果页；列表页仅会导入服务端实际可读取的笔记。任务按“发现链接 → 正文和图片解析 → Agent 整理 → 人工确认入库”执行。</p>
                </div>
                <span class="collection-loading">小红书</span>
              </div>
              <label class="collection-field">小红书公开 URL
                <input v-model="collectionDraft.sourceUrl" type="url" required maxlength="2000" placeholder="https://www.xiaohongshu.com/explore/..." />
              </label>
              <div class="collection-options-grid">
                <label class="collection-field compact-field">最多导入数量
                  <input v-model.number="collectionDraft.requestedLimit" type="number" min="1" max="50" inputmode="numeric" />
                </label>
                <label class="collection-option-toggle">
                  <input v-model="collectionDraft.includeImages" type="checkbox" />
                  <span>
                    <strong>识别多张图片文字</strong>
                    <small>把图片中的面试流程、题目和复盘补进正文</small>
                  </span>
                </label>
                <p class="collection-human-review-note">每条候选都会先由 Agent 整理为可编辑正文；只有点击“保存到面经库”后才会建立检索索引。</p>
              </div>
            </section>

            <section v-if="collectionJob" class="collection-job-card xiaohongshu-job-card" :class="`job-${collectionJob.status}`">
              <div class="collection-job-heading">
                <div>
                  <span>任务状态</span>
                  <strong>{{ collectionJobStatusText(collectionJob.status) }}</strong>
                </div>
                <em>{{ Math.round(collectionProgress.percent) }}%</em>
              </div>
              <div class="collection-stage-track" aria-label="采集阶段">
                <span v-for="(stage, index) in ['发现链接', '读取正文', 'OCR 图片', 'Agent 整理', '人工确认入库']" :key="stage" :class="{ active: collectionProgress.currentIndex >= index, current: collectionProgress.currentIndex === index && !isCollectionTerminal(collectionJob.status) }">
                  <i>{{ index + 1 }}</i>
                  {{ stage }}
                </span>
              </div>
              <div class="collection-progress-line" aria-hidden="true"><i :style="{ width: `${collectionProgress.percent}%` }"></i></div>
              <p>{{ collectionProgress.detail || collectionJob.error_message || '任务正在后台执行，完成后会自动刷新候选与入库结果。' }}</p>
            </section>

            <section v-if="collectionJob" class="collection-summary-grid" aria-label="采集结果统计">
              <div><span>发现链接</span><strong>{{ collectionAnalysisSummary.discovered }}</strong></div>
              <div><span>有效面经</span><strong>{{ collectionAnalysisSummary.valid }}</strong></div>
              <div><span>已写入</span><strong>{{ collectionAnalysisSummary.imported }}</strong></div>
              <div><span>采集不完整</span><strong>{{ collectionAnalysisSummary.incomplete }}</strong></div>
              <div><span>已过滤 / 失败</span><strong>{{ collectionAnalysisSummary.rejected + collectionAnalysisSummary.failed }}</strong></div>
            </section>

            <section v-if="collectionCandidates.length" class="candidate-list xiaohongshu-candidate-list">
              <header class="candidate-list-header">
                <div>
                  <p class="section-label">COLLECTION RESULTS</p>
                  <h3>采集结果</h3>
                </div>
                <span>{{ collectionCandidates.length }} 条候选</span>
              </header>
              <article v-for="candidate in collectionCandidates" :key="candidate.id" class="candidate-card xiaohongshu-candidate-card">
                <header>
                  <div>
                    <p class="section-label">{{ candidate.source_platform || '小红书' }}</p>
                    <h3>{{ candidate.title || '未命名笔记' }}</h3>
                    <a v-if="candidate.source_url" :href="candidate.source_url" target="_blank" rel="noreferrer">打开来源 ↗</a>
                  </div>
                  <span class="candidate-status" :class="{ 'is-imported': candidateImportedExperienceId(candidate), 'is-valid': candidateValidity(candidate) === true, 'is-invalid': candidateValidity(candidate) === false, 'is-incomplete': candidateAnalysisStatus(candidate) === 'insufficient_source', 'is-failed': ['failed', 'blocked', 'empty'].includes(candidate.status) }">{{ candidateStatusText(candidate) }}</span>
                </header>
                <p v-if="candidate.excerpt || candidate.snippet" class="candidate-excerpt">{{ candidate.excerpt || candidate.snippet }}</p>
                <div v-if="candidateCompany(candidate) || candidateRole(candidate) || candidateConfidence(candidate)" class="candidate-analysis-meta">
                  <span v-if="candidateCompany(candidate)">公司：{{ candidateCompany(candidate) }}</span>
                  <span v-if="candidateRole(candidate)">岗位：{{ candidateRole(candidate) }}</span>
                  <span v-if="candidateConfidence(candidate)">置信度：{{ candidateConfidence(candidate) }}</span>
                </div>
                <p class="candidate-evidence">{{ candidateEvidenceText(candidate) }}</p>
                <p v-if="candidateIncompleteReason(candidate)" class="candidate-incomplete-reason">{{ candidateIncompleteReason(candidate) }}</p>
                <p v-if="candidateReason(candidate)" class="candidate-reason">{{ candidateReason(candidate) }}</p>
                <section v-if="canManuallySaveCandidate(candidate) && candidateDraft(candidate)" class="candidate-review">
                  <header class="candidate-review-heading">
                    <div>
                      <p class="section-label">HUMAN REVIEW</p>
                      <strong>核对正文后保存到面经库</strong>
                    </div>
                    <em>{{ candidateValidity(candidate) === true ? 'Agent 已提取' : '需人工核验' }}</em>
                  </header>
                  <p v-if="candidateValidity(candidate) !== true" class="candidate-review-hint">系统未确认这是一条完整面经；请核对正文来源与内容，确认后仍可手动保存。</p>
                  <label class="candidate-body-field">面经正文
                    <textarea v-model="candidateDrafts[candidate.id].markdownContent" spellcheck="false" :aria-label="`编辑 ${candidate.title || '候选面经'} 正文`"></textarea>
                  </label>
                  <div class="form-grid candidate-import-meta">
                    <label>公司名称<input v-model="candidateDrafts[candidate.id].companyName" placeholder="待归档公司" /></label>
                    <label>面试岗位<input v-model="candidateDrafts[candidate.id].roleName" placeholder="未识别岗位" /></label>
                  </div>
                  <details class="candidate-extra-fields">
                    <summary>补充信息（可选）</summary>
                    <div class="form-grid">
                      <label>面试日期<input v-model="candidateDrafts[candidate.id].interviewDate" type="date" /></label>
                      <label>标签<input v-model="candidateDrafts[candidate.id].tags" placeholder="一面，Java，系统设计" /></label>
                    </div>
                    <label>摘要<input v-model="candidateDrafts[candidate.id].summary" placeholder="说明这份面经的价值" /></label>
                  </details>
                  <footer>
                    <button type="button" class="primary-action" :disabled="collectionSubmitting" @click="importCollectionCandidate(candidate)">{{ collectionSubmitting ? '正在保存…' : '保存到面经库' }}</button>
                  </footer>
                </section>
                <details v-else-if="candidate.markdown_content" class="candidate-markdown">
                  <summary>查看已保存的面经正文</summary>
                  <pre>{{ candidate.markdown_content }}</pre>
                </details>
              </article>
            </section>

            <section v-else-if="collectionJob && !isCollectionTerminal(collectionJob.status)" class="collection-waiting-card">
              <span class="thinking-orbit"></span>
              <strong>正在整理候选资料</strong>
              <p>已开始处理，候选笔记和入库结果会在这里自动出现。</p>
            </section>

            <footer>
              <button type="button" class="quiet-action" :disabled="collectionSubmitting" @click="closeCollection">{{ collectionJob ? '关闭并后台继续' : '取消' }}</button>
              <button v-if="!collectionJob || isCollectionTerminal(collectionJob.status)" class="primary-action" :disabled="collectionSubmitting">
                {{ collectionSubmitting ? '正在创建任务…' : '开始采集并分析' }}
              </button>
            </footer>
          </form>

          <form v-else-if="collectionMode === 'keyword'" class="collection-form public-web-collection-form" @submit.prevent="submitKeywordCollection">
            <section class="collection-section public-web-source-section">
              <div class="collection-section-heading">
                <div>
                  <h3>从公开网页建立面经候选池</h3>
                  <p>系统只检索无需登录即可访问的 HTTPS 页面，不携带浏览器登录态。相同地址和相同正文都会跨任务去重，已入库内容只追加新的来源记录。</p>
                </div>
                <span class="collection-loading">默认 10 条</span>
              </div>
              <p v-if="collectionPlatforms.length && !publicWebAvailabilityState.ready" class="public-web-unavailable">{{ publicWebAvailabilityState.reason }}</p>
            </section>

            <div class="public-web-query-row">
              <label class="collection-field">检索关键词
                <input v-model="collectionDraft.keyword" required maxlength="120" :disabled="collectionSubmitting || (collectionJob && !isCollectionTerminal(collectionJob.status))" placeholder="例如：agent开发面经" />
              </label>
              <label class="collection-field compact-field">最多分析数量
                <input v-model.number="collectionDraft.requestedLimit" type="number" min="5" max="10" inputmode="numeric" :disabled="collectionSubmitting || (collectionJob && !isCollectionTerminal(collectionJob.status))" />
                <small>可设置 5–10 条；每批最多读取 10 个网页。单条失败不会影响其他有效面经立即入库。</small>
              </label>
            </div>

            <section v-if="collectionJob" class="collection-job-card public-web-job-card" :class="`job-${collectionJob.status}`">
              <div class="collection-job-heading">
                <div><span>任务状态</span><strong>{{ collectionJobStatusText(collectionJob.status) }}</strong></div>
                <em>{{ Math.round(publicWebCollectionProgress.percent) }}%</em>
              </div>
              <div class="collection-stage-track" aria-label="全网公开信息收集阶段">
                <span v-for="(stage, index) in PUBLIC_WEB_COLLECTION_STAGES" :key="stage" :class="{ active: publicWebCollectionProgress.currentIndex >= index, current: publicWebCollectionProgress.currentIndex === index && !isCollectionTerminal(collectionJob.status) }">
                  <i>{{ index + 1 }}</i>{{ stage }}
                </span>
              </div>
              <div class="collection-progress-line" aria-hidden="true"><i :style="{ width: `${publicWebCollectionProgress.percent}%` }"></i></div>
              <p>{{ publicWebCollectionProgress.detail || collectionJob.error_message || '任务正在后台处理；关闭窗口不会中断任务。' }}</p>
            </section>

            <section v-if="collectionJob" class="collection-summary-grid public-web-summary-grid" aria-label="公开网页采集结果统计">
              <div><span>发现地址</span><strong>{{ publicWebSummary.discovered }}</strong></div>
              <div><span>已知地址</span><strong>{{ publicWebSummary.knownUrl }}</strong></div>
              <div><span>已提取正文</span><strong>{{ publicWebSummary.scraped }}</strong></div>
              <div><span>重复正文</span><strong>{{ publicWebSummary.duplicate }}</strong></div>
              <div><span>有效面经</span><strong>{{ publicWebSummary.valid }}</strong></div>
              <div><span>新入库</span><strong>{{ publicWebSummary.imported }}</strong></div>
              <div><span>已过滤</span><strong>{{ publicWebSummary.filtered }}</strong></div>
              <div><span>失败</span><strong>{{ publicWebSummary.failed }}</strong></div>
            </section>

            <section v-if="collectionCandidates.length" class="candidate-list public-web-candidate-list">
              <header class="candidate-list-header">
                <div><p class="section-label">处理明细</p><h3>公开网页候选</h3></div>
                <span>{{ collectionCandidates.length }} 个地址</span>
              </header>
              <article v-for="candidate in collectionCandidates" :key="candidate.id" class="candidate-card public-web-candidate-card">
                <header>
                  <div>
                    <p class="section-label">{{ candidate.source_platform || '公开网页' }}</p>
                    <h3>{{ candidate.title || '未命名公开页面' }}</h3>
                    <a v-if="candidate.source_url" :href="candidate.source_url" target="_blank" rel="noreferrer">查看已记录来源 ↗</a>
                  </div>
                  <span class="candidate-status" :class="{ 'is-imported': candidateImportedExperienceId(candidate), 'is-valid': candidateValidity(candidate) === true, 'is-invalid': candidateValidity(candidate) === false, 'is-failed': candidate.status === 'failed' }">{{ candidateStatusText(candidate) }}</span>
                </header>
                <p v-if="candidate.snippet" class="candidate-excerpt">{{ candidate.snippet }}</p>
                <div v-if="candidateCompany(candidate) || candidateRole(candidate) || candidateConfidence(candidate)" class="candidate-analysis-meta">
                  <span v-if="candidateCompany(candidate)">公司：{{ candidateCompany(candidate) }}</span>
                  <span v-if="candidateRole(candidate)">岗位：{{ candidateRole(candidate) }}</span>
                  <span v-if="candidateConfidence(candidate)">置信度：{{ candidateConfidence(candidate) }}</span>
                </div>
                <p v-if="candidate.error_message" class="candidate-reason">{{ candidate.error_message }}</p>
                <details v-if="candidate.markdown_content" class="candidate-markdown"><summary>查看解析后的文本</summary><pre>{{ candidate.markdown_content }}</pre></details>
              </article>
            </section>

            <section v-else-if="collectionJob && !isCollectionTerminal(collectionJob.status)" class="collection-waiting-card">
              <span class="thinking-orbit"></span><strong>正在建立公开网页候选池</strong><p>候选地址、去重结果与入库状态会在这里自动出现。</p>
            </section>

            <footer>
              <button type="button" class="quiet-action" :disabled="collectionSubmitting" @click="closeCollection">{{ collectionJob && !isCollectionTerminal(collectionJob.status) ? '关闭并后台继续' : '取消' }}</button>
              <button v-if="!collectionJob || isCollectionTerminal(collectionJob.status)" class="primary-action" :disabled="collectionSubmitting || !publicWebAvailabilityState.ready">
                {{ collectionSubmitting ? '正在创建任务…' : '开始公开信息收集' }}
              </button>
            </footer>
          </form>

          <form v-else class="collection-form" @submit.prevent="submitUrlCollection">
            <section class="collection-section url-section">
              <div class="collection-section-heading">
                <div>
                  <h3>导入一篇公开页面</h3>
                  <p>仅访问你主动提交的公开 HTTPS 页面；不携带第三方登录态，不执行脚本，也不会保存网页原始 HTML。</p>
                </div>
              </div>
              <label class="collection-field">公开 URL
                <input v-model="collectionDraft.sourceUrl" type="url" required placeholder="https://example.com/interview-experience" />
              </label>
            </section>

            <footer v-if="!collectionCandidates.length">
              <button type="button" class="quiet-action" :disabled="collectionSubmitting" @click="closeCollection">取消</button>
              <button class="primary-action" :disabled="collectionSubmitting">{{ collectionSubmitting ? '正在读取页面…' : '读取并生成候选正文' }}</button>
            </footer>

            <section v-for="candidate in collectionCandidates" :key="candidate.id" class="candidate-card">
              <header>
                <div>
                  <p class="section-label">CANDIDATE ARTICLE</p>
                  <h3>{{ candidate.title || '未命名面经正文' }}</h3>
                  <a v-if="candidate.source_url" :href="candidate.source_url" target="_blank" rel="noreferrer">查看公开来源 ↗</a>
                </div>
                <span>{{ candidate.markdown_content?.length || 0 }} 字</span>
              </header>
              <p v-if="candidate.excerpt" class="candidate-excerpt">{{ candidate.excerpt }}</p>
              <details class="candidate-markdown">
                <summary>查看将要入库的 Markdown 正文</summary>
                <pre>{{ candidate.markdown_content }}</pre>
              </details>
              <div class="form-grid candidate-meta-grid">
                <label>公司名称<input v-model="collectionDraft.companyName" required placeholder="例如：字节跳动" /></label>
                <label>面试岗位<input v-model="collectionDraft.roleName" required placeholder="例如：后端开发工程师" /></label>
                <label>面试日期<input v-model="collectionDraft.interviewDate" type="date" /></label>
                <label>主题标签<input v-model="collectionDraft.tags" placeholder="一面，Java，系统设计" /></label>
              </div>
              <label class="collection-field">摘要（可选）
                <input v-model="collectionDraft.summary" placeholder="说明这份面经适合解决的问题" />
              </label>
              <footer>
                <button type="button" class="quiet-action" :disabled="collectionSubmitting" @click="closeCollection">稍后处理</button>
                <button type="button" class="primary-action" :disabled="collectionSubmitting" @click="importCollectionCandidate(candidate)">
                  {{ collectionSubmitting ? '正在入库…' : '确认并建立检索索引' }}
                </button>
              </footer>
            </section>
          </form>
        </section>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.interview-library-shell { display: flex; min-height: 0; flex: 1; flex-direction: column; gap: 16px; color: #263425; }
.library-header { display: flex; align-items: center; justify-content: space-between; gap: 20px; border: 1px solid #e3eade; border-radius: 12px; background: #fff; box-shadow: 0 8px 24px rgba(69, 87, 42, .05); padding: 11px 12px; }
.library-heading { display: flex; min-width: 0; align-items: center; gap: 10px; }
.library-heading > div { display: grid; min-width: 0; gap: 2px; }
.library-heading strong { color: #263425; font-size: 14px; font-weight: 800; }
.library-heading-mark { display: grid; width: 34px; height: 34px; flex: 0 0 auto; place-items: center; border-radius: 9px; background: #eef5df; color: #6e9232; font-size: 18px; font-weight: 850; }
.library-kicker,.section-label { margin: 0; color: #88a143; font-size: 10px; font-weight: 800; letter-spacing: .12em; }
.library-description { margin: 0; color: #7c8b75; font-size: 12px; line-height: 1.5; }
.library-actions,.experience-actions { display: flex; align-items: center; gap: 8px; }
.library-actions button { display: inline-flex; min-height: 36px; align-items: center; gap: 6px; justify-content: center; padding: 8px 12px; }
.library-actions button > span { font-size: 15px; line-height: 1; }
.library-actions .quiet-action { background: #f8faf5; }
.library-actions .primary-action { box-shadow: none; }
.danger-action { min-height: 42px; border: 1px solid #efb0a8; border-radius: 10px; background: #fff5f3; color: #b53a2d; padding: 10px 16px; font: inherit; font-weight: 800; cursor: pointer; }
.danger-action:hover:not(:disabled) { background: #ffeae6; }
.read-only-notice { max-width: 240px; color: #74829b; font-size: 13px; line-height: 1.5; text-align: right; }
.library-notice { margin: -4px 0 0; border: 1px solid #bdd7f5; border-radius: 10px; background: #f2f8ff; color: #145da8; padding: 9px 13px; font-size: 12px; font-weight: 750; }
.primary-action,.quiet-action,.tree-refresh,.close-button { border: 1px solid #dfe8d2; border-radius: 10px; cursor: pointer; font: inherit; font-weight: 750; transition: transform .16s ease, box-shadow .16s ease, background .16s ease; }
.primary-action { border-color: #8eae37; background: #91b236; color: white; box-shadow: 0 8px 16px rgba(112, 139, 37, .18); padding: 10px 15px; }
.quiet-action { background: #fff; color: #5d713b; padding: 9px 13px; }
.primary-action:hover:not(:disabled),.quiet-action:hover:not(:disabled),.tree-refresh:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 8px 16px rgba(73, 94, 38, .1); }
button:focus-visible,input:focus-visible,textarea:focus-visible { outline: 3px solid var(--ui-focus, rgba(0, 100, 221, .16)); outline-offset: 2px; }
button:disabled { cursor: wait; opacity: .62; }
.library-layout { display: grid; min-height: 0; flex: 1; grid-template-columns: minmax(250px, 295px) minmax(0, 1fr); gap: 14px; }
.interview-tree-pane,.interview-detail-pane { min-height: 0; border: 1px solid #e3eade; border-radius: 18px; background: #fff; box-shadow: 0 14px 32px rgba(69, 87, 42, .06); }
.interview-tree-pane { display: flex; flex-direction: column; overflow: hidden; padding: 17px 14px 14px; }
.tree-pane-header { display: flex; align-items: center; justify-content: space-between; padding: 0 4px 12px; }
.tree-pane-header strong { font-size: 17px; }
.tree-refresh { width: 30px; height: 30px; background: #f5f8ee; color: #668438; font-size: 18px; line-height: 1; }
.tree-search { display: flex; align-items: center; gap: 8px; border: 1px solid #e3e9d7; border-radius: 11px; background: #fbfcf8; padding: 0 10px; color: #8b9982; }
.tree-search input { width: 100%; border: 0; outline: 0; background: transparent; color: #30422b; padding: 10px 0; font: inherit; font-size: 13px; }
.company-tree { min-height: 0; margin-top: 13px; overflow: auto; padding: 0 1px 8px; }
.company-tree details { border-bottom: 1px solid #f0f3ed; padding: 7px 0; }
.company-tree summary { display: flex; align-items: center; gap: 8px; cursor: pointer; list-style: none; color: #344432; font-size: 14px; font-weight: 800; }
.company-tree summary::-webkit-details-marker { display: none; }
.tree-company-mark { color: #7aa33f; font-size: 16px; }
.company-tree summary em { margin-left: auto; border-radius: 999px; background: #eef5df; color: #719036; padding: 2px 7px; font-size: 11px; font-style: normal; }
.tree-leaves { display: grid; gap: 3px; margin: 5px 0 2px 12px; border-left: 1px solid #e1e8d9; padding-left: 8px; }
.tree-leaf { display: grid; grid-template-columns: 8px minmax(0, 1fr) 7px; align-items: center; gap: 6px; border: 0; border-radius: 9px; background: transparent; color: #536150; cursor: pointer; padding: 8px 6px; text-align: left; }
.tree-leaf:hover { background: #f7faef; }.tree-leaf.active { background: #edf6d8; color: #39541d; }
.leaf-line { height: 1px; background: #d4dfc9; }.leaf-copy { min-width: 0; }.leaf-copy strong,.leaf-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.leaf-copy strong { font-size: 12px; }.leaf-copy small { margin-top: 3px; color: #929d8c; font-size: 10px; }
.status-dot { width: 7px; height: 7px; border-radius: 99px; background: #b7c0b2; }.status-indexed { background: #85ad35; }.status-failed { background: #d16f6f; }.status-parsing,.status-indexing { background: #d69b36; }
.tree-placeholder { display: grid; gap: 7px; margin: auto 8px; color: #8a9684; font-size: 13px; line-height: 1.7; }.tree-placeholder strong { color: #52624e; }.inline-error { margin: 12px 4px 0; color: #bd5f5f; font-size: 12px; }
.interview-detail-pane { display: flex; min-width: 0; flex-direction: column; overflow: hidden; }
.detail-state { display: grid; place-content: center; justify-items: center; min-height: 360px; flex: 1; padding: 42px; color: #7e8d78; text-align: center; }.detail-state strong { margin: 12px 0 0; color: #3c5037; font-size: 17px; }.detail-state p { max-width: 360px; line-height: 1.7; }.error-state strong,.error-state p { color: #a84e4e; }
.thinking-orbit { width: 34px; height: 34px; border: 3px solid #e3ebd8; border-top-color: #88ab36; border-radius: 50%; animation: orbit .75s linear infinite; }@keyframes orbit { to { transform: rotate(360deg); } }
.library-empty-state { min-height: 420px; }.empty-index-mark { display: grid; width: 54px; height: 54px; place-items: center; border-radius: 18px 18px 18px 4px; background: #ebf4d4; color: #6e9832; font-size: 28px; font-weight: 800; }.library-empty-state h3 { margin: 9px 0 0; color: #334830; font-size: 24px; }.library-empty-state .primary-action { margin-top: 10px; }
.experience-header { display: flex; align-items: start; justify-content: space-between; gap: 22px; border-bottom: 1px solid #edf1e9; padding: 21px 24px 16px; }.experience-header h3 { margin: 4px 0 0; color: #273923; font-size: 23px; letter-spacing: -.035em; }.experience-meta { display: flex; flex-wrap: wrap; gap: 7px 12px; margin-top: 8px; color: #899588; font-size: 12px; }.experience-meta a { color: #668d38; font-weight: 700; text-decoration: none; }
.index-status { border-radius: 999px; background: #eef6df; color: #5f8730; padding: 5px 9px; font-size: 12px; font-weight: 800; }.index-failed { background: #fff0ef; color: #b65757; }.index-parsing,.index-indexing { background: #fff5df; color: #a26d1f; }
.evidence-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)) minmax(160px, 1.5fr); gap: 12px; border-bottom: 1px solid #eef2e9; background: #fbfcf8; padding: 11px 24px; }.evidence-strip > div { min-width: 0; }.evidence-strip span,.evidence-strip em { display: block; color: #99a492; font-size: 10px; font-style: normal; }.evidence-strip strong { display: block; overflow: hidden; margin-top: 3px; color: #53634e; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.tag-list { display: flex; flex-wrap: wrap; align-content: center; gap: 4px; }.tag-list span { border-radius: 5px; background: #edf4df; color: #6e8b37; padding: 3px 6px; }
.reading-layout,.editor-layout { min-height: 0; flex: 1; overflow: auto; padding: 30px 32px 38px; }.summary-note { max-width: 900px; margin: 0 auto 20px; border: 1px solid #dce9ca; border-left: 4px solid #89ad39; border-radius: 0 13px 13px 0; background: linear-gradient(90deg, #f4f9e9, #fbfdf8); padding: 15px 19px; }.summary-note p { margin: 0 0 5px; color: #6f9133; font-size: 11px; font-weight: 850; letter-spacing: .08em; }.summary-note strong { color: #385134; font-size: 15px; line-height: 1.75; }.markdown-reading { max-width: 900px; margin: 0 auto; border: 1px solid #edf1e8; border-radius: 16px; background: #fff; box-shadow: 0 12px 32px rgba(55, 72, 49, .05); color: #344333; padding: 32px clamp(22px, 4vw, 54px) 42px; font-family: "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 16px; line-height: 1.95; word-break: break-word; }.reading-heading { margin: 34px 0 13px; color: #263a25; font-weight: 800; letter-spacing: -.02em; line-height: 1.35; }.reading-heading:first-child { margin-top: 0; }.markdown-reading h2 { border-bottom: 1px solid #e7eddc; padding-bottom: 9px; font-size: 22px; }.markdown-reading h3 { border-left: 3px solid #8eaf3b; padding-left: 11px; font-size: 18px; }.markdown-reading h4 { color: #54703d; font-size: 16px; }.reading-paragraph { margin: 0 0 17px; color: #40503e; }.reading-list { margin: 7px 0 19px; padding-left: 1.5em; color: #40503e; }.reading-list li { margin: 7px 0; padding-left: 4px; }.reading-ordered li::marker { color: #769b39; font-weight: 800; }.reading-divider { height: 1px; margin: 29px 0; border: 0; background: linear-gradient(90deg, #cddfba, #edf3e5 70%, transparent); }
.editor-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, .8fr); gap: 14px; }.editor-field { display: grid; gap: 6px; color: #4d6049; font-size: 12px; font-weight: 800; }.editor-field input,.editor-field textarea,.import-form input,.import-form textarea { width: 100%; box-sizing: border-box; border: 1px solid #dce6d6; border-radius: 9px; background: #fff; color: #30422d; font: inherit; font-size: 13px; }.editor-field input,.import-form input { padding: 10px 11px; }.markdown-field { grid-column: 1 / -1; }.markdown-field textarea { min-height: 460px; resize: vertical; padding: 14px; line-height: 1.75; }
.import-backdrop { position: fixed; z-index: 30; inset: 0; display: grid; place-items: center; background: rgba(38, 51, 32, .34); padding: 20px; }.import-dialog { position: relative; width: min(860px, 100%); max-height: min(860px, calc(100vh - 40px)); overflow: auto; border: 1px solid #e1e8d6; border-radius: 20px; background: #fff; box-shadow: 0 30px 90px rgba(21, 33, 17, .24); }.import-dialog > header { display: flex; align-items: start; justify-content: space-between; gap: 20px; border-bottom: 1px solid #edf1e8; padding: 24px 26px 18px; }.import-dialog h2 { margin: 5px 0 0; font-size: 25px; }.import-dialog header p:not(.library-kicker) { max-width: 620px; margin: 7px 0 0; color: #81907b; font-size: 13px; line-height: 1.6; }.close-button { width: 34px; height: 34px; flex: none; background: #fafbf7; color: #728067; font-size: 26px; font-weight: 400; line-height: 1; }.file-parse-overlay { position: absolute; z-index: 4; inset: 0; display: grid; place-items: center; background: rgba(249, 252, 245, .78); backdrop-filter: blur(3px); }.file-parse-progress-card { width: min(390px, calc(100% - 40px)); border: 1px solid #d7e6bf; border-radius: 16px; background: rgba(255, 255, 255, .96); box-shadow: 0 18px 48px rgba(48, 71, 33, .16); padding: 22px; }.progress-heading { display: flex; align-items: center; justify-content: space-between; color: #3f5635; font-size: 15px; font-weight: 800; }.progress-heading strong { color: #73953a; font-size: 20px; }.progress-track { height: 9px; overflow: hidden; margin: 16px 0 14px; border-radius: 999px; background: #e9f0df; }.progress-track i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #8eb33e, #b9d768); box-shadow: 0 0 14px rgba(131, 167, 52, .42); transition: width .35s ease; }.progress-phase { color: #40523a; font-size: 14px; }.file-parse-progress-card p { margin: 6px 0 0; color: #7c8974; font-size: 12px; line-height: 1.65; }
.import-tabs { display: flex; gap: 6px; border-bottom: 1px solid #edf1e8; padding: 12px 26px 0; }.import-tabs button { border: 0; border-bottom: 2px solid transparent; background: transparent; color: #899486; cursor: pointer; padding: 8px 12px 10px; font: inherit; font-size: 13px; font-weight: 800; }.import-tabs button.active { border-bottom-color: #87a93a; color: #55752b; }.import-form { display: grid; gap: 12px; padding: 20px 26px 24px; }.import-form label { display: grid; gap: 6px; color: #4a5a47; font-size: 12px; font-weight: 800; }.import-form textarea { min-height: 220px; resize: vertical; padding: 12px; line-height: 1.7; }.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.file-intake-intro { border-left: 3px solid #8bae39; border-radius: 0 9px 9px 0; background: #f6faed; padding: 11px 13px; }.file-intake-intro strong { color: #4c6a29; font-size: 13px; }.file-intake-intro p { margin: 4px 0 0; color: #718064; font-size: 12px; line-height: 1.6; }.file-picker { min-height: 68px; place-content: center; border: 1px dashed #b9cba2; border-radius: 10px; background: #f8fbf3; padding: 8px 12px; cursor: pointer; }.file-picker input { position: absolute; width: 1px; height: 1px; opacity: 0; }.file-picker span { color: #6b833e; font-size: 12px; }.file-import-strategy { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 14px; margin: 0; border: 1px solid #e2ead8; border-radius: 11px; background: #fcfdf9; padding: 11px 13px; }.file-import-strategy legend { padding: 0 5px; color: #4f6541; font-size: 12px; font-weight: 850; }.file-import-strategy label { display: flex; align-items: center; gap: 7px; color: #52634d; cursor: pointer; font-size: 12px; font-weight: 750; }.file-import-strategy input { width: auto; accent-color: #89ab38; }.file-import-strategy p { grid-column: 1 / -1; margin: 2px 0 0; color: #7a8972; font-size: 12px; line-height: 1.55; }.recognition-card { display: grid; gap: 5px; border: 1px solid #dcebc6; border-radius: 11px; background: #fbfdf7; padding: 12px 13px; }.recognition-card > div { display: flex; align-items: center; justify-content: space-between; gap: 10px; }.recognition-card strong { color: #48642b; font-size: 13px; }.recognition-card span { border-radius: 999px; background: #eaf4d7; color: #648331; padding: 4px 8px; font-size: 11px; font-weight: 800; }.recognition-card p { margin: 0; color: #71806b; font-size: 12px; line-height: 1.55; }.recognition-card .recognition-warning { color: #a37237; }.import-form footer { display: flex; justify-content: end; gap: 8px; margin-top: 4px; }.dialog-error,.dialog-success { margin: 14px 26px 0; border-radius: 9px; padding: 10px 12px; font-size: 13px; }.dialog-error { background: #fff1ef; color: #ab5252; }.dialog-success { background: #eef7df; color: #5f8830; }
.collection-backdrop { position: fixed; z-index: 31; inset: 0; display: grid; place-items: center; background: rgba(31, 43, 27, .43); padding: 20px; }.collection-dialog { width: min(960px, 100%); max-height: min(850px, calc(100vh - 40px)); overflow: auto; border: 1px solid #dfe8d2; border-radius: 20px; background: #fff; box-shadow: 0 30px 90px rgba(21, 33, 17, .28); }.collection-dialog-header { display: flex; align-items: start; justify-content: space-between; gap: 20px; border-bottom: 1px solid #edf1e8; padding: 24px 26px 18px; }.collection-dialog h2 { margin: 5px 0 0; font-size: 25px; }.collection-dialog-header p:not(.library-kicker) { max-width: 690px; margin: 7px 0 0; color: #71806c; font-size: 13px; line-height: 1.65; }.collection-tabs { background: #fbfcf8; }.collection-form { display: grid; gap: 16px; padding: 22px 26px 26px; }.collection-section { border: 1px solid #e6eddf; border-radius: 14px; background: #fbfcf9; padding: 16px; }.collection-section-heading { display: flex; align-items: start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }.collection-section h3,.candidate-card h3 { margin: 0; color: #314230; font-size: 16px; }.collection-section-heading p { max-width: 640px; margin: 5px 0 0; color: #7e8d77; font-size: 12px; line-height: 1.6; }.collection-loading { flex: none; border-radius: 999px; background: #edf4df; color: #6d8937; padding: 5px 9px; font-size: 11px; font-weight: 800; }.platform-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; }.platform-card { display: grid; gap: 4px; min-height: 78px; border: 1px solid #dfe8d5; border-radius: 11px; background: #fff; color: #405040; cursor: pointer; padding: 12px; text-align: left; transition: border-color .16s ease, background .16s ease, transform .16s ease; }.platform-card strong { font-size: 13px; }.platform-card span { color: #8b9786; font-size: 11px; }.platform-card:hover,.platform-card.active { border-color: #91b236; background: #f2f8e5; }.platform-card:hover { transform: translateY(-1px); }.collection-field { display: grid; gap: 6px; color: #4a5a47; font-size: 12px; font-weight: 800; }.collection-field input { width: 100%; box-sizing: border-box; border: 1px solid #dce6d6; border-radius: 9px; background: #fff; color: #30422d; padding: 10px 11px; font: inherit; font-size: 13px; }.compact-field { max-width: 220px; }.collection-form footer { display: flex; justify-content: end; gap: 8px; }.collection-job-card { display: grid; gap: 7px; border: 1px solid #dcebc6; border-radius: 12px; background: #f6faee; padding: 14px 16px; }.collection-job-card > div { display: flex; align-items: center; justify-content: space-between; gap: 14px; }.collection-job-card span { color: #7d8d6b; font-size: 11px; }.collection-job-card strong { color: #55752b; font-size: 13px; }.collection-job-card p { margin: 0; color: #5d6d56; font-size: 12px; line-height: 1.6; }.candidate-card { display: grid; gap: 14px; border: 1px solid #dce8cb; border-radius: 14px; background: #fcfdf9; padding: 17px; }.candidate-card > header { display: flex; align-items: start; justify-content: space-between; gap: 14px; }.candidate-card header > div { display: grid; gap: 5px; }.candidate-card header > span { flex: none; border-radius: 999px; background: #edf4df; color: #688436; padding: 5px 8px; font-size: 11px; font-weight: 800; }.candidate-card a { color: #638331; font-size: 12px; font-weight: 750; text-decoration: none; }.candidate-excerpt { margin: 0; color: #596856; font-size: 13px; line-height: 1.7; }.candidate-markdown { border-radius: 10px; background: #f6f8f3; color: #586656; }.candidate-markdown summary { cursor: pointer; padding: 10px 12px; font-size: 12px; font-weight: 800; }.candidate-markdown pre { max-height: 240px; overflow: auto; margin: 0; border-top: 1px solid #e7ece0; padding: 12px; color: #52604f; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; line-height: 1.65; white-space: pre-wrap; word-break: break-word; }.candidate-meta-grid { margin-top: 2px; }.candidate-meta-grid label { display: grid; gap: 6px; color: #4a5a47; font-size: 12px; font-weight: 800; }.candidate-meta-grid input { width: 100%; box-sizing: border-box; border: 1px solid #dce6d6; border-radius: 9px; background: #fff; color: #30422d; padding: 10px 11px; font: inherit; font-size: 13px; }
.xiaohongshu-import-form { gap: 14px; }.xiaohongshu-source-section { background: linear-gradient(135deg, #fbfdf7, #f4f9ea); }.collection-options-grid { display: grid; grid-template-columns: minmax(150px, .55fr) 1fr 1fr; align-items: stretch; gap: 10px; margin-top: 12px; }.collection-options-grid .compact-field { max-width: none; }.collection-option-toggle { display: flex; align-items: center; gap: 9px; border: 1px solid #e0eacd; border-radius: 10px; background: rgba(255,255,255,.76); padding: 10px 12px; cursor: pointer; }.collection-option-toggle input { width: 16px; height: 16px; margin: 0; accent-color: #89ab38; }.collection-option-toggle span { display: grid; gap: 3px; }.collection-option-toggle strong { color: #4a623b; font-size: 12px; }.collection-option-toggle small { color: #809076; font-size: 11px; line-height: 1.45; }.xiaohongshu-job-card { position: sticky; z-index: 2; top: 0; gap: 12px; border-color: #d5e6b8; background: rgba(248, 252, 240, .98); box-shadow: 0 10px 22px rgba(76, 104, 44, .08); }.collection-job-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }.collection-job-heading > div { display: grid; gap: 3px; }.collection-job-heading em { border-radius: 999px; background: #e5f1cf; color: #5f8430; padding: 5px 9px; font-size: 12px; font-style: normal; font-weight: 850; }.collection-stage-track { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 6px; }.collection-stage-track span { display: grid; grid-template-columns: 19px minmax(0, 1fr); align-items: center; gap: 5px; color: #a0aa98; font-size: 10px; font-weight: 750; line-height: 1.25; }.collection-stage-track i { display: grid; width: 19px; height: 19px; place-items: center; border: 1px solid #d9e3ce; border-radius: 50%; background: #fff; color: #91a087; font-size: 10px; font-style: normal; }.collection-stage-track span.active { color: #668c30; }.collection-stage-track span.active i { border-color: #a7c766; background: #eaf5d4; color: #5f8430; }.collection-stage-track span.current i { box-shadow: 0 0 0 4px rgba(145,178,54,.14); }.collection-progress-line { height: 7px; overflow: hidden; border-radius: 999px; background: #e3ecd6; }.collection-progress-line i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #8bae39, #bbd76f); transition: width .35s ease; }.collection-summary-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 9px; }.collection-summary-grid > div { display: grid; gap: 4px; border: 1px solid #e6eddf; border-radius: 11px; background: #fbfcf9; padding: 11px 12px; }.collection-summary-grid span { color: #899783; font-size: 11px; }.collection-summary-grid strong { color: #4d6d2b; font-size: 18px; }.candidate-list { display: grid; gap: 10px; }.candidate-list-header { display: flex; align-items: end; justify-content: space-between; gap: 14px; padding: 2px 2px 0; }.candidate-list-header h3 { margin: 4px 0 0; color: #394c35; font-size: 17px; }.candidate-list-header > span { color: #7b8d6f; font-size: 12px; }.xiaohongshu-candidate-card { gap: 11px; }.candidate-status { white-space: nowrap; }.candidate-card header > .candidate-status.is-valid { background: #edf7d9; color: #5a8130; }.candidate-card header > .candidate-status.is-imported { background: #dff0c5; color: #4c7823; }.candidate-card header > .candidate-status.is-invalid { background: #fff1e8; color: #a26a34; }.candidate-card header > .candidate-status.is-incomplete { background: #fff7df; color: #9a702e; }.candidate-card header > .candidate-status.is-failed { background: #fff0ef; color: #ad5757; }.candidate-analysis-meta { display: flex; flex-wrap: wrap; gap: 6px; }.candidate-analysis-meta span { border-radius: 999px; background: #eef5e1; color: #668236; padding: 4px 8px; font-size: 11px; }.candidate-evidence { margin: 0; color: #798b70; font-size: 11px; line-height: 1.5; }.candidate-incomplete-reason { margin: 0; border-left: 3px solid #e0ab4e; border-radius: 0 8px 8px 0; background: #fff9e9; color: #85652d; padding: 8px 10px; font-size: 12px; line-height: 1.55; }.candidate-reason { margin: 0; border-left: 3px solid #e7b36e; border-radius: 0 8px 8px 0; background: #fff9ef; color: #8a683f; padding: 8px 10px; font-size: 12px; line-height: 1.55; }.collection-waiting-card { display: grid; place-items: center; gap: 8px; min-height: 128px; border: 1px dashed #cbdcb4; border-radius: 13px; background: #fbfdf8; padding: 20px; color: #6d7e63; text-align: center; }.collection-waiting-card .thinking-orbit { width: 25px; height: 25px; border-width: 2px; }.collection-waiting-card strong { color: #516747; font-size: 14px; }.collection-waiting-card p { margin: 0; font-size: 12px; }
.experience-context-strip { display: flex; flex-wrap: wrap; align-items: center; gap: 7px 12px; border-bottom: 1px solid #eef2e9; background: #fbfcf8; color: #879380; padding: 10px 24px; font-size: 11px; }.experience-context-strip > span { white-space: nowrap; }.experience-context-strip .tag-list { margin-left: auto; }.editor-layout { grid-template-columns: minmax(0, 1fr); max-width: 1040px; margin: 0 auto; }.editor-canvas { gap: 10px; border: 1px solid #dfe8d4; border-radius: 15px; background: #fff; box-shadow: 0 12px 26px rgba(57, 75, 42, .05); padding: 18px; }.editor-canvas > span { display: flex; align-items: center; gap: 9px; color: #354b31; font-size: 15px; }.editor-canvas > span i { color: #87a23f; font-size: 10px; font-style: normal; letter-spacing: .1em; }.editor-canvas > span em { margin-left: auto; border-radius: 999px; background: #edf5df; color: #668335; padding: 4px 8px; font-size: 11px; font-style: normal; }.editor-canvas textarea { min-height: 560px; border-color: #e5ecde; border-radius: 11px; background: #fcfdfb; padding: 17px; font-family: "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 15px; line-height: 1.85; }.editor-support-panel { border: 1px solid #e3eadc; border-radius: 12px; background: #fafcf8; color: #54654e; }.editor-support-panel summary { display: flex; align-items: center; justify-content: space-between; cursor: pointer; padding: 12px 14px; font-size: 12px; font-weight: 800; }.editor-support-panel summary span { color: #95a18d; font-weight: 600; }.editor-support-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; border-top: 1px solid #ebf0e6; padding: 13px 14px 15px; }.file-progress-context { display: flex; justify-content: space-between; gap: 10px; margin-top: 15px; color: #70815f; font-size: 11px; }.file-progress-context strong { overflow: hidden; color: #4d653b; text-align: right; text-overflow: ellipsis; white-space: nowrap; }.file-progress-list { display: grid; gap: 5px; max-height: 150px; overflow: auto; margin: 9px 0 0; padding: 0; list-style: none; }.file-progress-list li { display: grid; grid-template-columns: 21px minmax(0, 1fr) auto; align-items: center; gap: 7px; border-radius: 8px; background: #f8faf5; padding: 6px 8px; color: #71806b; font-size: 11px; }.file-progress-list li > span { display: grid; width: 20px; height: 20px; place-items: center; border-radius: 50%; background: #e8efdf; color: #6a843b; font-size: 10px; font-weight: 800; }.file-progress-list strong { overflow: hidden; color: #566751; text-overflow: ellipsis; white-space: nowrap; }.file-progress-list em { color: #88977e; font-size: 10px; font-style: normal; }.file-progress-list .is-parsing { background: #f2f8e7; }.file-progress-list .is-completed > span { background: #dcefc1; }.file-progress-list .is-failed { background: #fff4f1; }.file-progress-list .is-failed > span,.file-progress-list .is-failed em { color: #b45b52; }.collection-human-review-note { margin: 0; border: 1px solid #dce9c8; border-left: 3px solid #8eae39; border-radius: 0 10px 10px 0; background: #f8fbf2; color: #637759; padding: 11px 12px; font-size: 12px; line-height: 1.6; }.candidate-review { display: grid; gap: 11px; border-top: 1px solid #e4ecdc; padding-top: 14px; }.candidate-review-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.candidate-review-heading > div { display: grid; gap: 3px; }.candidate-review-heading strong { color: #3d5436; font-size: 14px; }.candidate-review-heading em { flex: none; border-radius: 999px; background: #eff5e5; color: #6c8839; padding: 4px 8px; font-size: 11px; font-style: normal; font-weight: 750; }.candidate-review-hint { margin: 0; border-left: 3px solid #d8a550; background: #fff9ec; color: #86682d; padding: 8px 10px; font-size: 12px; line-height: 1.6; }.candidate-body-field { display: grid; gap: 7px; color: #47603e; font-size: 12px; font-weight: 850; }.candidate-body-field textarea { width: 100%; min-height: 340px; box-sizing: border-box; resize: vertical; border: 1px solid #dbe7d2; border-radius: 11px; background: #fff; color: #334732; padding: 14px; font-family: "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 14px; line-height: 1.8; }.candidate-import-meta label,.candidate-extra-fields label { display: grid; gap: 6px; color: #53644d; font-size: 12px; font-weight: 750; }.candidate-import-meta input,.candidate-extra-fields input { width: 100%; box-sizing: border-box; border: 1px solid #dce6d6; border-radius: 9px; background: #fff; color: #30422d; padding: 10px 11px; font: inherit; font-size: 13px; }.candidate-extra-fields { border: 1px solid #e5ecdf; border-radius: 10px; background: #fafcf8; }.candidate-extra-fields summary { cursor: pointer; color: #607455; padding: 10px 11px; font-size: 12px; font-weight: 800; }.candidate-extra-fields[open] { padding-bottom: 11px; }.candidate-extra-fields[open] summary { margin-bottom: 10px; border-bottom: 1px solid #ebf0e6; }.candidate-extra-fields > .form-grid,.candidate-extra-fields > label { margin-right: 11px; margin-left: 11px; }.candidate-extra-fields > label { margin-top: 10px; }.candidate-review footer { display: flex; justify-content: flex-end; }
@media (max-width: 900px) { .library-layout { grid-template-columns: 1fr; }.interview-tree-pane { max-height: 290px; }.evidence-strip { grid-template-columns: 1fr 1fr; }.library-header,.experience-header { align-items: start; flex-direction: column; }.experience-actions { width: 100%; justify-content: space-between; }.library-title-row { align-items: start; flex-direction: column; gap: 3px; }.editor-layout,.form-grid,.platform-grid { grid-template-columns: 1fr; }.collection-dialog { max-height: calc(100vh - 24px); }.collection-backdrop { padding: 12px; }.collection-dialog-header,.collection-form { padding-right: 18px; padding-left: 18px; }.collection-tabs { padding-right: 18px; padding-left: 18px; } }
@media (max-width: 640px) { .library-page { gap:12px; }.library-header,.experience-header { padding:16px; }.evidence-strip { grid-template-columns:1fr; padding:12px 16px; }.reading-layout,.editor-layout { padding:18px 14px 26px; }.markdown-reading { border-radius:12px; padding:22px 18px 28px; font-size:15px; line-height:1.85; }.file-import-strategy { grid-template-columns:1fr; }.import-backdrop,.collection-backdrop { align-items:end; padding:0; }.import-dialog,.collection-dialog { width:100%; max-height:92dvh; border-radius:20px 20px 0 0; }.import-dialog > header,.collection-dialog-header { padding:18px 18px 14px; }.import-tabs,.collection-tabs { overflow-x:auto; padding-right:18px; padding-left:18px; }.import-form,.collection-form { padding:18px; }.import-form footer,.collection-form footer { flex-wrap:wrap; }.import-form footer button,.collection-form footer button { min-height:44px; flex:1; }.file-parse-progress-card { width:calc(100% - 28px); padding:18px; } }
@media (max-width: 900px) {
  .collection-options-grid { grid-template-columns: 1fr 1fr; }
  .collection-options-grid .compact-field { grid-column: 1 / -1; max-width: 220px; }
  .collection-stage-track { grid-template-columns: repeat(3, minmax(0, 1fr)); row-gap: 10px; }
}
@media (max-width: 640px) {
  .collection-options-grid,.collection-summary-grid { grid-template-columns: 1fr 1fr; }
  .collection-options-grid .compact-field { grid-column: 1 / -1; max-width: none; }
  .collection-stage-track { grid-template-columns: 1fr 1fr; }
  .xiaohongshu-job-card { top: -1px; margin: 0 -2px; border-radius: 12px; }
  .candidate-list-header,.candidate-card > header { align-items: start; flex-direction: column; }
  .candidate-card header > .candidate-status { align-self: start; }
}
/* 编辑态与阅读态共用 900px 内容视口，避免编辑器因 grid 收缩成窄栏。 */
.editor-layout {
  width: 100%;
  max-width: none;
  box-sizing: border-box;
  align-content: start;
}

.editor-identity-panel,
.editor-canvas,
.editor-support-panel {
  width: min(900px, 100%);
  box-sizing: border-box;
  margin-inline: auto;
}

.editor-identity-panel {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  border: 1px solid #d7e4f6;
  border-radius: 14px;
  background: #f8fbff;
  padding: 16px 18px;
}

.editor-identity-panel .editor-field,
.editor-canvas > span {
  color: #17223b;
}

.editor-identity-panel .editor-field input,
.editor-support-fields .editor-field input,
.editor-canvas textarea {
  border-color: #d7e4f6;
  background: #fff;
  color: #17223b;
}

.editor-canvas {
  border-color: #d7e4f6;
  box-shadow: 0 12px 32px rgba(24, 62, 112, .05);
}

.editor-canvas > span em {
  background: #eaf3ff;
  color: #004aa8;
}

.editor-canvas textarea {
  min-height: 560px;
  padding: 32px clamp(22px, 4vw, 54px) 42px;
  font-size: 16px;
  line-height: 1.95;
}

.editor-support-panel {
  border-color: #d7e4f6;
  background: #f4f8fe;
  color: #526078;
}

.editor-support-panel summary span {
  color: #7b879c;
}

.editor-support-fields {
  border-color: #d7e4f6;
}

@media (prefers-reduced-motion: reduce) { .thinking-orbit { animation: none; } }
/* 手机阅读、编辑与详情使用同一纵向页面滚动；仅面经树保留受控滚动。 */
@media (max-width:900px) {
  .interview-library-shell,
  .library-layout {
    min-height:0;
  }

  .interview-tree-pane {
    max-height:320px;
  }

  .company-tree {
    max-height:218px;
  }

  .interview-detail-pane,
  .reading-layout,
  .editor-layout {
    overflow:visible;
  }

  .reading-layout,
  .editor-layout {
    flex:none;
  }

  .editor-identity-panel {
    grid-template-columns: 1fr;
  }
}

@media (max-width:640px) {
  .library-actions,
  .experience-actions {
    width:100%;
    flex-wrap:wrap;
  }

  .library-actions > button,
  .experience-actions > button {
    min-height:44px;
    flex:1 1 132px;
  }

  .import-dialog,
  .collection-dialog {
    max-height:calc(100dvh - env(safe-area-inset-top));
  }

  .import-form,
  .collection-form {
    padding-bottom:max(24px, env(safe-area-inset-bottom));
  }

  .import-dialog > header,
  .collection-dialog-header {
    padding-top:max(18px, env(safe-area-inset-top));
  }

  .close-button {
    width:44px;
    height:44px;
  }

  .experience-context-strip {
    align-items: flex-start;
    padding: 11px 16px;
  }

  .experience-context-strip .tag-list {
    width: 100%;
    margin-left: 0;
  }

  .editor-canvas {
    border-right: 0;
    border-left: 0;
    border-radius: 0;
    padding: 14px;
  }

  .editor-canvas textarea {
    min-height: 440px;
    padding: 13px;
    font-size: 15px;
  }

  .editor-support-fields,
  .candidate-import-meta {
    grid-template-columns: 1fr;
  }

  .candidate-review-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .candidate-body-field textarea {
    min-height: 360px;
    font-size: 15px;
  }

  .candidate-review footer .primary-action {
    width: 100%;
    min-height: 44px;
  }
}

.experience-source-ledger {
  margin: 12px 24px 0;
  border: 1px solid #dce6d3;
  border-radius: 12px;
  background: #fbfcf9;
}

.experience-source-ledger > summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  padding: 11px 13px;
  color: #4c6045;
  font-size: 12px;
  font-weight: 850;
}

.experience-source-ledger > summary span {
  color: #899784;
  font-size: 11px;
  font-weight: 650;
}

.experience-source-list {
  display: grid;
  gap: 1px;
  border-top: 1px solid #e8eee3;
  background: #e8eee3;
}

.experience-source-list a {
  display: grid;
  gap: 4px;
  min-width: 0;
  background: #fff;
  padding: 11px 13px;
  color: #5d713b;
  text-decoration: none;
}

.experience-source-list a:hover { background: #f7faF2; }
.experience-source-list strong { color: #40533c; font-size: 12px; }
.experience-source-list span { overflow: hidden; color: #72806e; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.experience-source-list small { color: #91a087; font-size: 10px; }

.public-web-collection-form { gap: 14px; }
.public-web-source-section {
  position: relative;
  overflow: hidden;
  border-color: #cfe0bd;
  background: linear-gradient(135deg, #f8fcf2, #eef6e6);
}
.public-web-source-section::before {
  position: absolute;
  inset: 0 auto 0 0;
  width: 5px;
  background: repeating-linear-gradient(180deg, #729a35 0 9px, #b8d083 9px 15px);
  content: '';
}
.public-web-unavailable { margin: 10px 0 0; border-radius: 9px; background: #fff1ef; color: #a84f4f; padding: 9px 11px; font-size: 12px; }
.public-web-query-row { display: grid; grid-template-columns: minmax(0, 1fr) 230px; gap: 12px; }
.public-web-query-row .compact-field { max-width: none; }
.public-web-query-row small { color: #81907b; font-size: 11px; font-weight: 600; line-height: 1.5; }
.public-web-job-card { position: sticky; z-index: 2; top: 0; gap: 12px; border-color: #cedfb9; background: rgba(248, 252, 242, .98); box-shadow: 0 10px 22px rgba(76, 104, 44, .08); }
.public-web-summary-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.public-web-summary-grid > div { border-left: 3px solid #a9c673; }
.public-web-candidate-card { gap: 11px; }

@media (max-width: 700px) {
  .public-web-query-row { grid-template-columns: 1fr; }
  .public-web-summary-grid { grid-template-columns: 1fr 1fr; }
  .experience-source-ledger { margin-right: 16px; margin-left: 16px; }
}
</style>
