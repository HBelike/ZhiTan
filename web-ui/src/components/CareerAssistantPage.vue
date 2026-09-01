<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ElConfigProvider from 'element-plus/es/components/config-provider/index.mjs'
import ElPagination from 'element-plus/es/components/pagination/index.mjs'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/es/components/pagination/style/css'
import CareerContextRail from './CareerContextRail.vue'
import CareerContextSetupDialog from './CareerContextSetupDialog.vue'
import CareerGreetingDialog from './CareerGreetingDialog.vue'
import CareerJobSearchDialog from './CareerJobSearchDialog.vue'
import CareerMessageContent from './CareerMessageContent.vue'
import CareerContextMeter from './CareerContextMeter.vue'
import { toTargetRolePayload } from '../job-library-target-role.js'
import { isAssessmentPending } from '../career-assessment-view.js'
import { openBrowserInterviewMaster } from '../browser-interview-launcher.js'
import { openOnlineAssessmentAssistant } from '../online-assessment/launcher.js'
import {
  buildComposerHighlightSegments,
  composerReferencePresent,
  selectedInterviewReferencePresent
} from '../career-composer-highlights.js'
import {
  firstServerTurn,
  removeServerTurn,
  restoreServerTurns,
  upsertServerTurn
} from '../career-turn-client-queue.js'
import {
  createTurnObservationCoordinator,
  shouldReconcileConversationTurn
} from '../career-turn-observation.js'
import {
  observedTurnStatus,
  presentTurnProgress,
  publicTurnError,
  submissionFeedback,
  turnStatusText
} from '../career-turn-presentation.js'
import {
  normalizeTurnLimit,
  turnLimitPresentation
} from '../career-turn-limit.js'
import {
  DEFAULT_HISTORY_PAGE_SIZE,
  HISTORY_PAGE_SIZE_OPTIONS,
  normalizeHistoryPage,
  normalizeHistoryPageTarget,
  pageRequestUrl,
  resolveHistoryPageSize
} from '../career-history-pagination.js'
import {
  getCareerLayoutMode,
  getPanelAfterLayoutChange
} from '../career-responsive-layout.js'
import { createChatAutoScroller } from '../career-chat-auto-scroll.js'

defineProps({
  interviewMasterVisible: {
    type: Boolean,
    default: true
  },
  onlineAssessmentVisible: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['navigate'])

const conversations = ref([])
const candidateProfiles = ref([])
const selectedConversation = ref(null)
const conversationContext = ref(null)
const messages = ref([])
const modelProfiles = ref([])
const freeModelCatalog = ref([])
const freeModelCatalogError = ref('')
const loading = ref(false)
const sending = ref(false)
const pendingTurns = ref([])
const creating = ref(false)
const savingModel = ref(false)
const testingConnection = ref(false)
const loadingFreeModelCatalog = ref(false)
const showJobUrl = ref(false)
const showModelDialog = ref(false)
const showModelApiKey = ref(false)
const modelDialogMode = ref('list')
const modelSetupReturnMode = ref('list')
const testedConnectionFingerprint = ref('')
const connectionTestMessage = ref('')
const connectionTestError = ref('')
const connectionSaveError = ref('')
const connectionConfigRef = ref(null)
const composerHighlightLayer = ref(null)
const messageListRef = ref(null)
const latestMessageRef = ref(null)
const composerDisplayText = ref('')
const messageText = ref('')
const interviewMentionQuery = ref('')
const interviewMentionResults = ref([])
const selectedInterviewReferences = ref([])
const skillMentionQuery = ref('')
const skillMentionResults = ref([])
const selectedSkillInvocations = ref([])
const jobUrl = ref('')
const resumeFile = ref(null)
const resumeInput = ref(null)
const selectionMode = ref('free_quota_first')
const selectedProfileId = ref('')
const feedback = ref('')
const errorMessage = ref('')
const lastTurn = ref(null)
const streamedAssistantText = ref('')
const streamStatus = ref('')
const streamProgress = ref([])
const activeTemporaryMessageId = ref('')
const activeObservedTurnId = ref('')
const submittingTurnCount = ref(0)
const modelForm = ref(emptyModelForm())
const conversationMenuId = ref('')
const renamingConversationId = ref('')
const renameTitleDraft = ref('')
const deletingConversationId = ref('')
const conversationActionLoadingId = ref('')
const showContextSetup = ref(false)
const contextSetupInitial = ref(null)
const showJobSearch = ref(false)
const showGreetingDialog = ref(false)
const savingJobSearch = ref(false)
const jobSearchError = ref('')
const pendingJobLibraryTarget = ref(null)
const jobSearchButton = ref(null)
const greetingButton = ref(null)
const historyCollapsed = ref(false)
const historyPage = ref(1)
const historyPageSize = ref(DEFAULT_HISTORY_PAGE_SIZE)
const historyTotal = ref(0)
const historyLoading = ref(false)
const contextRailWidth = ref(620)
const resizingContextRail = ref(false)
const careerWorkspaceRef = ref(null)
const historyDrawerTrigger = ref(null)
const contextDrawerTrigger = ref(null)
const historyDrawerClose = ref(null)
const contextDrawerClose = ref(null)
const workspaceWidth = ref(typeof window === 'undefined' ? 1600 : window.innerWidth)
const layoutMode = ref(getCareerLayoutMode(workspaceWidth.value))
const compactPanel = ref('')
const contextUsage = ref(null)
const contextUsageLoading = ref(false)
const contextUsageRevision = ref(0)
const turnLimit = ref(normalizeTurnLimit())

const HISTORY_COLLAPSED_STORAGE_KEY = 'career-assistant-history-collapsed'
const HISTORY_PAGE_SIZE_STORAGE_KEY = 'career-assistant-history-page-size'
const CONTEXT_RAIL_WIDTH_STORAGE_KEY = 'career-assistant-context-rail-width'
const CONTEXT_RAIL_MIN_WIDTH = 360
const CONTEXT_RAIL_MAX_WIDTH = 760
const CONTEXT_RAIL_DEFAULT_WIDTH = 620
const CONTEXT_USAGE_DEBOUNCE_MS = 250
const historyTotalPages = computed(() => Math.max(1, Math.ceil(historyTotal.value / historyPageSize.value)))
const composerHighlightSegments = computed(() => buildComposerHighlightSegments(
  composerDisplayText.value,
  selectedSkillInvocations.value,
  selectedInterviewReferences.value
))

const visibleContextRailWidth = computed(() => {
  if (layoutMode.value !== 'wide') return Math.min(contextRailWidth.value, 420)
  const historyWidth = historyCollapsed.value ? 48 : 280
  const minimumChatWidth = 480
  const availableWidth = workspaceWidth.value - historyWidth - minimumChatWidth - 28
  return Math.min(contextRailWidth.value, Math.max(CONTEXT_RAIL_MIN_WIDTH, availableWidth))
})

const hasCandidateContext = computed(() => Boolean(conversationContext.value?.candidate_profile))
const hasTargetContext = computed(() => Boolean(conversationContext.value?.target_role))
const contextButtonLabel = computed(() => {
  const completed = Number(hasCandidateContext.value) + Number(hasTargetContext.value)
  return completed ? `简历上传 ${completed}/2` : '简历上传'
})
const turnLimitView = computed(() => turnLimitPresentation(turnLimit.value))
const turnInputBlocked = computed(() => sending.value || turnLimitView.value.blocked)
const composerPlaceholder = computed(() => (
  turnLimitView.value.blocked
    ? '本对话已达到 30 轮，请开启新对话'
    : (selectedConversation.value ? '输入你想咨询的问题…' : '请先开启一个对话')
))
const welcomeMessage = computed(() => {
  if (hasCandidateContext.value && hasTargetContext.value) {
    return '基准简历和目标岗位已经就绪。你可以询问岗位差距、简历表达、面试问题，或结合面经准备回答。'
  }
  if (hasCandidateContext.value) {
    return '已加载你的基准简历。你可以直接咨询经历表达、简历优化或面试准备，也可以稍后补充目标岗位。'
  }
  if (hasTargetContext.value) {
    return '已加载目标岗位。你可以直接咨询岗位要求和准备重点，也可以稍后补充简历以获得个性化匹配分析。'
  }
  return '现在可以直接和我聊求职问题。简历和 JD 都是可选资料，你可以随时在下方补充，后续回答会自动使用最新资料。'
})

let errorToastTimer = null
let conversationSelectionRequestId = 0
let historyPageRequestId = 0
let temporaryMessageSequence = 0
let turnRecoveryPollTimer = null
let assessmentPollTimer = null
let interviewMentionDebounceTimer = null
let interviewMentionRequestId = 0
let skillMentionDebounceTimer = null
let skillMentionRequestId = 0
let contextUsageRequestId = 0
let contextUsageDebounceTimer = null
let contextUsageAbortController = null
let careerResizeObserver = null
const turnObservationCoordinator = createTurnObservationCoordinator()
const chatAutoScroller = createChatAutoScroller({
  getContainer: () => messageListRef.value,
  getTarget: () => latestMessageRef.value
})

function dismissError() {
  errorMessage.value = ''
  if (errorToastTimer) {
    clearTimeout(errorToastTimer)
    errorToastTimer = null
  }
}

watch(errorMessage, (message) => {
  if (errorToastTimer) clearTimeout(errorToastTimer)
  errorToastTimer = null
  if (!message) return
  errorToastTimer = setTimeout(() => {
    errorMessage.value = ''
    errorToastTimer = null
  }, 3000)
})

watch(
  [sending, streamedAssistantText, streamStatus, streamProgress, feedback, () => messages.value.length],
  ([isSending], [wasSending]) => {
    if (isSending || wasSending) chatAutoScroller.request()
  },
  { deep: true, flush: 'post' }
)

onBeforeUnmount(() => {
  if (errorToastTimer) clearTimeout(errorToastTimer)
  if (interviewMentionDebounceTimer) clearTimeout(interviewMentionDebounceTimer)
  if (skillMentionDebounceTimer) clearTimeout(skillMentionDebounceTimer)
  cancelContextUsageRequest()
  document.removeEventListener('pointerdown', handleConversationActionPointerDown)
  document.removeEventListener('keydown', handleCareerPanelKeydown)
  careerResizeObserver?.disconnect()
  careerResizeObserver = null
  chatAutoScroller.cancel()
  stopContextRailResize()
  stopActiveTurnObservation()
  stopTurnRecoveryPolling()
  stopAssessmentPolling()
})

function closeConversationActions() {
  conversationMenuId.value = ''
  renamingConversationId.value = ''
  renameTitleDraft.value = ''
  deletingConversationId.value = ''
}

function toggleHistoryPanel() {
  historyCollapsed.value = !historyCollapsed.value
  closeConversationActions()
  window.localStorage.setItem(HISTORY_COLLAPSED_STORAGE_KEY, historyCollapsed.value ? '1' : '0')
}

function applyHistoryPage(payload, fallbackPage = historyPage.value) {
  const normalized = normalizeHistoryPage(payload, fallbackPage, historyPageSize.value)
  conversations.value = normalized.items
  historyPage.value = normalized.page
  historyPageSize.value = normalized.pageSize
  historyTotal.value = normalized.total
  return normalized
}

async function loadConversationPage(page = historyPage.value) {
  const requestedPage = Math.max(1, Number(page) || 1)
  const requestId = ++historyPageRequestId
  historyLoading.value = true
  closeConversationActions()
  try {
    const payload = await requestJson(pageRequestUrl(requestedPage, historyPageSize.value))
    if (requestId !== historyPageRequestId) return
    const normalized = applyHistoryPage(payload, requestedPage)
    if (normalized.page !== requestedPage && normalized.total > 0) {
      await loadConversationPage(normalized.page)
    }
  } catch (error) {
    if (requestId === historyPageRequestId) {
      errorMessage.value = error instanceof Error ? error.message : '会话列表加载失败'
    }
  } finally {
    if (requestId === historyPageRequestId) historyLoading.value = false
  }
}

function goToHistoryPage(page) {
  const target = normalizeHistoryPageTarget(page, historyTotalPages.value)
  if (target === historyPage.value || historyLoading.value) return
  void loadConversationPage(target)
}

function changeHistoryPageSize(pageSize) {
  const nextPageSize = resolveHistoryPageSize(pageSize, window.innerHeight)
  if (nextPageSize === historyPageSize.value || historyLoading.value) return
  const firstVisibleIndex = (historyPage.value - 1) * historyPageSize.value
  historyPageSize.value = nextPageSize
  historyPage.value = Math.floor(firstVisibleIndex / nextPageSize) + 1
  window.localStorage.setItem(HISTORY_PAGE_SIZE_STORAGE_KEY, String(nextPageSize))
  void loadConversationPage(historyPage.value)
}

function clampContextRailWidth(width) {
  return Math.min(CONTEXT_RAIL_MAX_WIDTH, Math.max(CONTEXT_RAIL_MIN_WIDTH, Math.round(width)))
}

function persistContextRailWidth() {
  window.localStorage.setItem(CONTEXT_RAIL_WIDTH_STORAGE_KEY, String(contextRailWidth.value))
}

let contextRailResizeStartX = 0
let contextRailResizeStartWidth = CONTEXT_RAIL_DEFAULT_WIDTH

function handleContextRailPointerMove(event) {
  if (!resizingContextRail.value) return
  contextRailWidth.value = clampContextRailWidth(contextRailResizeStartWidth + contextRailResizeStartX - event.clientX)
}

function stopContextRailResize() {
  if (!resizingContextRail.value) return
  resizingContextRail.value = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  window.removeEventListener('pointermove', handleContextRailPointerMove)
  window.removeEventListener('pointerup', stopContextRailResize)
  window.removeEventListener('pointercancel', stopContextRailResize)
  persistContextRailWidth()
}

function startContextRailResize(event) {
  if (event.button !== 0) return
  contextRailResizeStartX = event.clientX
  contextRailResizeStartWidth = contextRailWidth.value
  resizingContextRail.value = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  event.currentTarget.setPointerCapture?.(event.pointerId)
  window.addEventListener('pointermove', handleContextRailPointerMove)
  window.addEventListener('pointerup', stopContextRailResize)
  window.addEventListener('pointercancel', stopContextRailResize)
}

function resizeContextRailBy(delta) {
  contextRailWidth.value = clampContextRailWidth(contextRailWidth.value + delta)
  persistContextRailWidth()
}

function resetContextRailWidth() {
  contextRailWidth.value = CONTEXT_RAIL_DEFAULT_WIDTH
  persistContextRailWidth()
}

function syncCareerWorkspaceWidth(width) {
  const nextWidth = Math.max(0, Math.round(Number(width) || 0))
  const nextMode = getCareerLayoutMode(nextWidth)
  compactPanel.value = getPanelAfterLayoutChange(layoutMode.value, nextMode, compactPanel.value)
  workspaceWidth.value = nextWidth
  layoutMode.value = nextMode
}

function toggleCompactPanel(panel) {
  if (compactPanel.value === panel) {
    closeCompactPanel()
    return
  }
  compactPanel.value = panel
  nextTick(() => {
    const closeButton = panel === 'history' ? historyDrawerClose.value : contextDrawerClose.value
    closeButton?.focus()
  })
}

function closeCompactPanel(restoreFocus = true) {
  const closingPanel = compactPanel.value
  if (!closingPanel) return
  compactPanel.value = ''
  if (!restoreFocus) return
  nextTick(() => {
    const trigger = closingPanel === 'history' ? historyDrawerTrigger.value : contextDrawerTrigger.value
    trigger?.focus()
  })
}

function handleCareerPanelKeydown(event) {
  if (event.key !== 'Escape' || !compactPanel.value) return
  event.preventDefault()
  closeCompactPanel()
}

function handleConversationActionPointerDown(event) {
  if (!conversationMenuId.value) return
  if (event.target instanceof Element && event.target.closest('.conversation-item')) return
  closeConversationActions()
}

function clearInterviewMentions() {
  interviewMentionQuery.value = ''
  interviewMentionResults.value = []
  selectedInterviewReferences.value = []
}

function clearSkillInvocations() {
  skillMentionQuery.value = ''
  skillMentionResults.value = []
  selectedSkillInvocations.value = []
}

function activeInterviewMention(text) {
  const match = text.match(/(?:^|\s)@([^\s@]{1,80})$/)
  return match ? match[1].trim() : ''
}

async function loadInterviewMentionResults(query) {
  const requestId = ++interviewMentionRequestId
  try {
    const payload = await requestJson(`/api/career/interview-library/mentions?query=${encodeURIComponent(query)}`)
    if (requestId !== interviewMentionRequestId || interviewMentionQuery.value !== query) return
    interviewMentionResults.value = payload.items ?? []
  } catch {
    if (requestId === interviewMentionRequestId) interviewMentionResults.value = []
  }
}

function scheduleInterviewMentionSearch(text) {
  const query = activeInterviewMention(text)
  interviewMentionQuery.value = query
  interviewMentionResults.value = []
  if (interviewMentionDebounceTimer) clearTimeout(interviewMentionDebounceTimer)
  interviewMentionDebounceTimer = null
  if (!query) return
  interviewMentionDebounceTimer = setTimeout(() => {
    void loadInterviewMentionResults(query)
  }, 180)
}

function selectInterviewMention(experience) {
  if (selectedInterviewReferences.value.some((item) => item.id === experience.id)) {
    interviewMentionResults.value = []
    return
  }
  if (selectedInterviewReferences.value.length >= 5) {
    errorMessage.value = '单轮最多引用 5 份面经资料。'
    return
  }
  selectedInterviewReferences.value = [...selectedInterviewReferences.value, experience]
  messageText.value = messageText.value.replace(/(?:^|\s)@[^\s@]{1,80}$/, ' ').trimEnd()
  messageText.value = `${messageText.value}${messageText.value ? ' ' : ''}@${experience.company_name}·${experience.role_name} `
  interviewMentionQuery.value = ''
  interviewMentionResults.value = []
}

function removeInterviewMention(experienceId) {
  const experience = selectedInterviewReferences.value.find((item) => item.id === experienceId)
  selectedInterviewReferences.value = selectedInterviewReferences.value.filter((item) => item.id !== experienceId)
  if (experience) {
    const token = `@${experience.company_name}·${experience.role_name}`
    messageText.value = messageText.value
      .replace(token, ' ')
      .replace(/\s{2,}/g, ' ')
      .trim()
  }
}

function activeSkillInvocation(text) {
  const match = text.match(/(?:^|\s)\/([A-Za-z0-9_-]{0,80})$/)
  return match ? { query: match[1].trim() } : null
}

async function loadSkillMentionResults(query) {
  const requestId = ++skillMentionRequestId
  try {
    const payload = await requestJson(`/api/career/skills/mentions?query=${encodeURIComponent(query)}`)
    if (requestId !== skillMentionRequestId || skillMentionQuery.value !== query) return
    skillMentionResults.value = payload.items ?? []
  } catch {
    if (requestId === skillMentionRequestId) skillMentionResults.value = []
  }
}

function scheduleSkillMentionSearch(text) {
  const invocation = activeSkillInvocation(text)
  skillMentionQuery.value = invocation?.query ?? ''
  skillMentionResults.value = []
  if (skillMentionDebounceTimer) clearTimeout(skillMentionDebounceTimer)
  skillMentionDebounceTimer = null
  if (!invocation) return
  skillMentionDebounceTimer = setTimeout(() => {
    void loadSkillMentionResults(invocation.query)
  }, 120)
}

function selectSkillMention(skill) {
  if (selectedSkillInvocations.value.some((item) => item.id === skill.id)) {
    skillMentionResults.value = []
    return
  }
  if (selectedSkillInvocations.value.length >= 3) {
    errorMessage.value = '单轮最多调用 3 个 Skill。'
    return
  }
  const trigger = '/'
  selectedSkillInvocations.value = [...selectedSkillInvocations.value, skill]
  messageText.value = messageText.value.replace(/(?:^|\s)\/[A-Za-z0-9_-]{0,80}$/, ' ').trimEnd()
  messageText.value = `${messageText.value}${messageText.value ? ' ' : ''}${trigger}${skill.name} `
  skillMentionQuery.value = ''
  skillMentionResults.value = []
  interviewMentionResults.value = []
}

function removeSkillInvocation(skillId) {
  const skill = selectedSkillInvocations.value.find((item) => item.id === skillId)
  selectedSkillInvocations.value = selectedSkillInvocations.value.filter((item) => item.id !== skillId)
  if (skill) {
    const escapedName = skill.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    messageText.value = messageText.value
      .replace(new RegExp(`(^|\\s)/${escapedName}(?=\\s|$)`, 'gi'), ' ')
      .replace(/\s{2,}/g, ' ')
      .trim()
  }
}

function skillInvocationPresent(text, skill) {
  return composerReferencePresent(text, `/${skill.name}`)
}

function activeSelectedSkillInvocations(text) {
  return selectedSkillInvocations.value.filter((skill) => skillInvocationPresent(text, skill))
}

watch(messageText, (text) => {
  composerDisplayText.value = text
  const activeInterviews = selectedInterviewReferences.value.filter((experience) => (
    selectedInterviewReferencePresent(text, experience)
  ))
  if (activeInterviews.length !== selectedInterviewReferences.value.length) {
    selectedInterviewReferences.value = activeInterviews
  }
  const activeSkills = activeSelectedSkillInvocations(text)
  if (activeSkills.length !== selectedSkillInvocations.value.length) {
    selectedSkillInvocations.value = activeSkills
  }
  scheduleInterviewMentionSearch(text)
  scheduleSkillMentionSearch(text)
})

function syncComposerHighlightInput(event) {
  // v-model 会等中文输入法完成组合后再更新；显示层需要逐个 input 即时跟随。
  composerDisplayText.value = event.currentTarget.value
}

function syncComposerHighlightScroll(event) {
  if (!composerHighlightLayer.value) return
  composerHighlightLayer.value.scrollTop = event.currentTarget.scrollTop
  composerHighlightLayer.value.scrollLeft = event.currentTarget.scrollLeft
}

const providerOptions = [
  { key: 'deepseek', label: 'DeepSeek', short: 'DS', detail: '中文技术与推理模型', websiteUrl: 'https://platform.deepseek.com', apiBaseUrl: 'https://api.deepseek.com', defaultModelId: 'deepseek-v4-pro', modelHint: '例如：deepseek-v4-pro', vision: false },
  { key: 'groq', label: 'Groq', short: 'G', detail: '低延迟开源模型推理服务', websiteUrl: 'https://console.groq.com', apiBaseUrl: 'https://api.groq.com/openai/v1', defaultModelId: 'openai/gpt-oss-20b', modelHint: '例如：openai/gpt-oss-20b', vision: false },
  { key: 'mistral', label: 'Mistral AI', short: 'MI', detail: '提供有限月度用量的 Free mode', websiteUrl: 'https://console.mistral.ai', apiBaseUrl: 'https://api.mistral.ai/v1', defaultModelId: 'mistral-small-latest', modelHint: '例如：mistral-small-latest', vision: false },
  { key: 'openrouter', label: 'OpenRouter', short: 'OR', detail: '可路由到当前免费模型', websiteUrl: 'https://openrouter.ai', apiBaseUrl: 'https://openrouter.ai/api/v1', defaultModelId: 'openrouter/free', modelHint: '例如：openrouter/free', vision: false },
  { key: 'gemini', label: 'Google Gemini', short: 'GM', detail: '免费层支持文字与图片理解', websiteUrl: 'https://aistudio.google.com', apiBaseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', defaultModelId: 'gemini-3.7-flash', modelHint: '例如：gemini-3.7-flash', vision: true },
  { key: 'qwen', label: '阿里云百炼 Qwen', short: 'QW', detail: 'DashScope 新用户限时免费额度', websiteUrl: 'https://bailian.console.aliyun.com', apiBaseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', defaultModelId: 'qwen3.6-flash', modelHint: '例如：qwen3.6-flash', vision: true },
  { key: 'siliconflow', label: 'SiliconFlow', short: 'SF', detail: '价格页明确标记的当前免费模型', websiteUrl: 'https://cloud.siliconflow.cn', apiBaseUrl: 'https://api.siliconflow.cn/v1', defaultModelId: 'THUDM/GLM-Z1-9B-0414', modelHint: '例如：THUDM/GLM-Z1-9B-0414', vision: false },
  { key: 'modelscope', label: 'ModelScope', short: 'MS', detail: '使用魔力值兑换 API-Inference 体验', websiteUrl: 'https://www.modelscope.cn', apiBaseUrl: 'https://api-inference.modelscope.cn/v1', defaultModelId: 'deepseek-ai/DeepSeek-V4-Pro', modelHint: '例如：deepseek-ai/DeepSeek-V4-Pro', vision: false },
  { key: 'nvidia', label: 'NVIDIA NIM', short: 'NV', detail: 'NVIDIA 推理服务', websiteUrl: 'https://build.nvidia.com', apiBaseUrl: 'https://integrate.api.nvidia.com/v1', modelHint: '例如：meta/llama-3.3-70b-instruct', vision: true },
  { key: 'tokenhub', label: '腾讯云 TokenHub', short: 'TH', detail: '多模型统一网关', websiteUrl: 'https://console.cloud.tencent.com/tokenhub/', apiBaseUrl: 'https://tokenhub.tencentmaas.com/v1', defaultModelId: 'deepseek-v4-flash', modelHint: '例如：deepseek-v4-flash', vision: true },
  { key: 'baidu-qianfan', label: '百度千帆', short: 'BQ', detail: 'ERNIE 与开源模型 OpenAI-compatible 接口', websiteUrl: 'https://console.bce.baidu.com/qianfan', apiBaseUrl: 'https://qianfan.baidubce.com/v2', defaultModelId: 'ernie-3.5-8k', modelHint: '例如：ernie-3.5-8k', vision: true },
  { key: 'hunyuan', label: '腾讯混元', short: 'HY', detail: '混元 OpenAI-compatible 接口', websiteUrl: 'https://console.cloud.tencent.com/hunyuan', apiBaseUrl: 'https://api.hunyuan.cloud.tencent.com/v1', defaultModelId: 'hunyuan-lite', modelHint: '例如：hunyuan-lite', vision: true },
  { key: 'zhipu', label: '智谱 AI', short: 'ZP', detail: 'GLM 系列 OpenAI-compatible 接口', websiteUrl: 'https://open.bigmodel.cn', apiBaseUrl: 'https://open.bigmodel.cn/api/paas/v4', modelHint: '例如：glm-4-flash', vision: true },
  { key: 'minimax', label: 'MiniMax', short: 'MM', detail: 'MiniMax 通用模型服务', websiteUrl: 'https://platform.minimaxi.com', apiBaseUrl: 'https://api.minimaxi.com/v1', modelHint: '例如：MiniMax-Text-01', vision: false },
  { key: 'volcengine', label: '火山方舟', short: 'ARK', detail: '方舟兼容 Chat Completions 接口', websiteUrl: 'https://console.volcengine.com/ark', apiBaseUrl: 'https://ark.cn-beijing.volces.com/api/v3', modelHint: '填写已创建推理接入点 ID', vision: true },
  { key: 'custom', label: '自定义兼容服务商', short: '+', detail: '手动填写 OpenAI-compatible 地址', websiteUrl: '', apiBaseUrl: '', modelHint: '例如：provider/model-name', vision: false }
]

const selectedProfile = computed(() =>
  modelProfiles.value.find((item) => item.profile.id === selectedProfileId.value) ?? null
)
const readyModelProfiles = computed(() =>
  modelProfiles.value.filter((item) => item.readiness === 'ready')
)
const readyFreeModelProfiles = computed(() =>
  readyModelProfiles.value.filter((item) => item.profile.cost_tier === 'free_quota')
)
const readyPaidModelProfiles = computed(() =>
  readyModelProfiles.value.filter((item) => item.profile.cost_tier === 'paid')
)
const readyLocalModelProfiles = computed(() =>
  readyModelProfiles.value.filter((item) => item.profile.cost_tier === 'local')
)
const hasReadyModel = computed(() => readyModelProfiles.value.length > 0)
const hasReadyFreeModel = computed(() => readyFreeModelProfiles.value.length > 0)
const modelSelectionValue = computed(() => {
  if (selectionMode.value === 'free_quota_first' && hasReadyFreeModel.value) return 'free_quota_first'
  return selectedProfile.value?.readiness === 'ready' ? selectedProfileId.value : ''
})
const resolvedSelectedProfileId = computed(() => (
  selectionMode.value === 'free_quota_first'
    ? (readyFreeModelProfiles.value[0]?.profile.id ?? '')
    : selectedProfileId.value
))
const modelLabel = computed(() =>
  selectionMode.value === 'free_quota_first' && hasReadyFreeModel.value
    ? '免费模型自动选择'
    : selectedProfile.value
      ? modelPrimaryLabel(selectedProfile.value)
      : '尚未配置可用模型'
)
const modelDialogTitle = computed(() => ({
  list: '模型与连接',
  free: '申请并接入免费模型',
  setup: '添加或编辑模型连接'
}[modelDialogMode.value] ?? '模型与连接'))

function emptyModelForm() {
  return {
    profileKey: '', displayName: '', providerKey: 'deepseek', modelId: '', apiKey: '',
    apiBaseUrl: 'https://api.deepseek.com', websiteUrl: 'https://platform.deepseek.com',
    costTier: 'paid', priority: 100, text: true, vision: false, tools: false
  }
}

const activeProviderOption = computed(() =>
  providerOptions.find((item) => item.key === modelForm.value.providerKey) ?? providerOptions[0]
)
const usesPresetEndpoint = computed(() => activeProviderOption.value.key !== 'custom')

function openModelDialog() {
  modelDialogMode.value = 'list'
  showModelDialog.value = true
}

function openFreeModelDirectory() {
  modelDialogMode.value = 'free'
  showModelDialog.value = true
}

function hideModelApiKey() {
  showModelApiKey.value = false
}

function closeModelDialog() {
  hideModelApiKey()
  showModelDialog.value = false
  connectionTestMessage.value = ''
  connectionTestError.value = ''
  connectionSaveError.value = ''
  testedConnectionFingerprint.value = ''
}

function createModelConnection() {
  hideModelApiKey()
  modelForm.value = emptyModelForm()
  connectionTestMessage.value = ''
  connectionTestError.value = ''
  connectionSaveError.value = ''
  testedConnectionFingerprint.value = ''
  modelSetupReturnMode.value = 'list'
  modelDialogMode.value = 'setup'
}

function chooseProvider(provider) {
  hideModelApiKey()
  modelForm.value = {
    ...emptyModelForm(),
    providerKey: provider.key,
    profileKey: `${provider.key}-${Date.now()}`,
    displayName: `${provider.label} 模型连接`,
    modelId: provider.defaultModelId ?? '',
    apiBaseUrl: provider.apiBaseUrl,
    websiteUrl: provider.websiteUrl,
    vision: provider.vision
  }
  testedConnectionFingerprint.value = ''
  connectionTestMessage.value = ''
  connectionTestError.value = ''
  connectionSaveError.value = ''
  nextTick(() => connectionConfigRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

function configureFreeModel(offer, model) {
  const provider = providerOptions.find((item) => item.key === offer.provider_key)
  if (!provider) {
    errorMessage.value = `暂不支持自动配置 ${offer.display_name}，请使用自定义兼容服务商。`
    return
  }
  chooseProvider(provider)
  modelForm.value = {
    ...modelForm.value,
    displayName: `${offer.display_name} · ${model.display_name}`,
    modelId: model.model_id,
    apiBaseUrl: offer.api_base_url || provider.apiBaseUrl,
    websiteUrl: offer.website_url || provider.websiteUrl,
    costTier: 'free_quota',
    vision: Boolean(model.supports_vision),
    tools: Boolean(model.supports_tools)
  }
  modelSetupReturnMode.value = 'free'
  modelDialogMode.value = 'setup'
}

function configuredCatalogProfile(offer, model) {
  return modelProfiles.value.find((item) =>
    item.profile.provider_key === offer.provider_key && item.profile.model_id === model.model_id
  ) ?? null
}

function configureCatalogModel(offer, model) {
  const existing = configuredCatalogProfile(offer, model)
  if (existing) {
    editModelConnection(existing, 'free')
    return
  }
  configureFreeModel(offer, model)
}

function catalogModelStatus(offer, model) {
  const existing = configuredCatalogProfile(offer, model)
  return existing ? readinessText(existing.readiness) : '未配置 API Key'
}

function providerOption(providerKey) {
  return providerOptions.find((item) => item.key === providerKey) ?? {
    label: providerKey,
    short: providerKey.slice(0, 2).toUpperCase()
  }
}

function modelPrimaryLabel(item) {
  const profile = item?.profile ?? item
  if (!profile) return '未知模型'
  return `${providerOption(profile.provider_key).label} · ${profile.model_id}`
}

function normalizeModelLabel(value) {
  return String(value ?? '').trim().toLocaleLowerCase().replace(/[\s·._-]+/g, '')
}

function modelSecondaryLabel(item) {
  const profile = item?.profile ?? item
  if (!profile) return ''
  const displayName = String(profile.display_name ?? '').trim()
  if (!displayName) return ''
  const providerLabel = providerOption(profile.provider_key).label
  const normalizedDisplayName = normalizeModelLabel(displayName)
  const redundantLabels = new Set([
    normalizeModelLabel(providerLabel),
    normalizeModelLabel(profile.model_id),
    normalizeModelLabel(`${providerLabel} 模型连接`),
    normalizeModelLabel(`${providerLabel} model connection`),
    normalizeModelLabel(modelPrimaryLabel(profile))
  ])
  return redundantLabels.has(normalizedDisplayName) ? '' : displayName
}

function modelChoiceLabel(item) {
  const secondaryLabel = modelSecondaryLabel(item)
  return secondaryLabel
    ? `${modelPrimaryLabel(item)}（${secondaryLabel}）`
    : modelPrimaryLabel(item)
}

async function loadFreeModelCatalog() {
  loadingFreeModelCatalog.value = true
  freeModelCatalogError.value = ''
  try {
    const catalog = await requestJson('/api/career/free-model-catalog')
    freeModelCatalog.value = catalog.items ?? []
    return true
  } catch (error) {
    freeModelCatalog.value = []
    const reason = error instanceof Error ? error.message : '服务暂时不可用'
    freeModelCatalogError.value = `无法读取免费模型目录：${reason}。请重试，或返回模型连接手动添加服务商。`
    return false
  } finally {
    loadingFreeModelCatalog.value = false
  }
}

function editModelConnection(item, returnMode = 'list') {
  hideModelApiKey()
  const profile = item.profile
  modelForm.value = {
    profileKey: profile.profile_key,
    displayName: profile.display_name,
    providerKey: profile.provider_key,
    modelId: profile.model_id,
    apiKey: '',
    apiBaseUrl: providerApiBaseUrl(profile.provider_key, profile.api_base_url),
    websiteUrl: providerWebsiteUrl(profile.provider_key, profile.provider_website_url),
    costTier: profile.cost_tier,
    priority: profile.priority,
    text: profile.capabilities.includes('text'),
    vision: profile.capabilities.includes('vision'),
    tools: profile.capabilities.includes('tools')
  }
  testedConnectionFingerprint.value = ''
  connectionTestMessage.value = '为了保护密钥，修改连接后请重新填写 API Key 并测试。'
  connectionTestError.value = ''
  connectionSaveError.value = ''
  modelSetupReturnMode.value = returnMode
  modelDialogMode.value = 'setup'
}

function openResumePicker() {
  resumeInput.value?.click()
}

function handleResumeFile(event) {
  const selectedFile = event.target.files?.[0] ?? null
  if (!selectedFile) return
  const supportedTypes = [
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/bmp',
    'image/x-ms-bmp',
    'image/tiff'
  ]
  if (!supportedTypes.includes(selectedFile.type) || selectedFile.size > 10 * 1024 * 1024) {
    errorMessage.value = '仅支持 10MB 以内的 PDF、Word、Excel、JPG、PNG、WebP、BMP 或 TIFF 文件。'
    event.target.value = ''
    return
  }
  resumeFile.value = selectedFile
  feedback.value = `已选择 ${selectedFile.name}，文件只会临时解析，任务结束后自动删除。`
}

function clearResumeFile() {
  resumeFile.value = null
  if (resumeInput.value) resumeInput.value.value = ''
}

function clearObservedTurnDisplay() {
  activeTemporaryMessageId.value = ''
  activeObservedTurnId.value = ''
  streamedAssistantText.value = ''
  streamStatus.value = ''
  streamProgress.value = []
}

function stopActiveTurnObservation() {
  turnObservationCoordinator.cancel()
  clearObservedTurnDisplay()
}

function resetConversationDraft() {
  // 清理只能服务当前浏览器会话的临时输入，避免跨会话串材料。
  stopActiveTurnObservation()
  messageText.value = ''
  jobUrl.value = ''
  showJobUrl.value = false
  clearResumeFile()
  clearInterviewMentions()
  clearSkillInvocations()
  pendingTurns.value = []
  sending.value = false
  lastTurn.value = null
}

function isActiveTurn(turn) {
  return turn?.status === 'queued' || turn?.status === 'running'
}

function stopTurnRecoveryPolling() {
  if (!turnRecoveryPollTimer) return
  clearTimeout(turnRecoveryPollTimer)
  turnRecoveryPollTimer = null
}

function scheduleTurnRecoveryPolling() {
  stopTurnRecoveryPolling()
  if (!selectedConversation.value
    || !shouldReconcileConversationTurn(lastTurn.value, pendingTurns.value)) return
  turnRecoveryPollTimer = setTimeout(() => {
    void refreshActiveConversationTurn()
  }, 3000)
}

async function restoreServerQueue(conversationId) {
  const payload = await requestJson(`/api/career/conversations/${conversationId}/active-turns`)
  if (selectedConversation.value?.id !== conversationId) return
  pendingTurns.value = restoreServerTurns(payload.items ?? [])
  if (pendingTurns.value.length > 0) {
    lastTurn.value = pendingTurns.value.at(-1)?.serverTurn ?? lastTurn.value
    void observeNextServerTurn()
  } else {
    syncSendingState()
    scheduleTurnRecoveryPolling()
  }
}

function restoreServerQueueSafely(conversationId) {
  return restoreServerQueue(conversationId).catch(() => {
    if (selectedConversation.value?.id !== conversationId) return
    if (isActiveTurn(lastTurn.value)) {
      sending.value = true
      scheduleTurnRecoveryPolling()
      return
    }
    syncSendingState()
  })
}

async function refreshActiveConversationTurn() {
  const conversationId = selectedConversation.value?.id
  if (!conversationId || pendingTurns.value.length > 0 || activeObservedTurnId.value) return
  try {
    const payload = await requestJson(`/api/career/conversations/${conversationId}`)
    if (selectedConversation.value?.id !== conversationId) return
    messages.value = payload.messages ?? []
    turnLimit.value = normalizeTurnLimit(payload.turn_limit)
    conversationContext.value = payload.context ?? null
    restoreConversationModelSelection(payload.last_model_selection)
    lastTurn.value = payload.latest_turn ?? null
    await restoreServerQueue(conversationId)
  } catch (error) {
    // 后台任务仍在运行时，短暂网络波动不应以弹窗打断用户；下一次轮询会继续尝试。
    if (!isActiveTurn(lastTurn.value)) return
  } finally {
    if (selectedConversation.value?.id === conversationId) scheduleTurnRecoveryPolling()
  }
}

function stopAssessmentPolling() {
  if (!assessmentPollTimer) return
  clearTimeout(assessmentPollTimer)
  assessmentPollTimer = null
}

function scheduleAssessmentPolling() {
  stopAssessmentPolling()
  if (!selectedConversation.value || !isAssessmentPending(conversationContext.value?.assessment)) return
  assessmentPollTimer = setTimeout(() => {
    void refreshAssessmentContext()
  }, 2000)
}

async function refreshAssessmentContext() {
  const conversationId = selectedConversation.value?.id
  if (!conversationId || !isAssessmentPending(conversationContext.value?.assessment)) return
  try {
    const payload = await requestJson(`/api/career/conversations/${conversationId}`)
    if (selectedConversation.value?.id !== conversationId) return
    conversationContext.value = payload.context ?? null
  } catch {
    // 分析任务仍在后台运行，短暂读取失败时继续轮询，不打断当前对话。
  } finally {
    if (selectedConversation.value?.id === conversationId) scheduleAssessmentPolling()
  }
}

watch(
  () => [selectedConversation.value?.id, conversationContext.value?.assessment?.status],
  scheduleAssessmentPolling,
)

watch(
  () => [
    selectedConversation.value?.id,
    resolvedSelectedProfileId.value,
    contextUsageRevision.value,
  ],
  scheduleContextUsageLoad,
)

function cancelContextUsageRequest() {
  if (contextUsageDebounceTimer) clearTimeout(contextUsageDebounceTimer)
  contextUsageDebounceTimer = null
  contextUsageAbortController?.abort()
  contextUsageAbortController = null
  contextUsageRequestId += 1
}

function scheduleContextUsageLoad() {
  cancelContextUsageRequest()
  contextUsage.value = null
  const conversationId = selectedConversation.value?.id
  const profileId = resolvedSelectedProfileId.value
  if (!conversationId || !profileId) {
    contextUsageLoading.value = false
    return
  }
  const request = {
    id: contextUsageRequestId,
    conversationId,
    profileId,
    controller: new AbortController()
  }
  contextUsageAbortController = request.controller
  contextUsageLoading.value = true
  contextUsageDebounceTimer = setTimeout(() => {
    contextUsageDebounceTimer = null
    void loadContextUsage(request)
  }, CONTEXT_USAGE_DEBOUNCE_MS)
}

async function loadContextUsage(request) {
  try {
    const payload = await requestJson(
      `/api/career/conversations/${request.conversationId}/context-usage?model_profile_id=${encodeURIComponent(request.profileId)}`,
      { signal: request.controller.signal }
    )
    if (request.id === contextUsageRequestId) {
      contextUsage.value = payload.context_usage ?? null
      turnLimit.value = normalizeTurnLimit(payload.turn_limit)
    }
  } catch (error) {
    if (request.id === contextUsageRequestId && error?.name !== 'AbortError') {
      contextUsage.value = null
    }
  } finally {
    if (request.id === contextUsageRequestId) {
      contextUsageLoading.value = false
      if (contextUsageAbortController === request.controller) {
        contextUsageAbortController = null
      }
    }
  }
}

function messageModelLabel(message) {
  const model = message?.model
  if (!model?.provider_key || !model?.model_id) return ''
  return `${providerOption(model.provider_key).label} · ${model.model_id}`
}

function useFreeQuotaFirstSelection() {
  if (!hasReadyFreeModel.value) {
    useFirstReadyModelSelection()
    return
  }
  selectionMode.value = 'free_quota_first'
  selectedProfileId.value = ''
}

function useFirstReadyModelSelection() {
  const firstReady = readyModelProfiles.value[0]
  if (!firstReady) {
    selectionMode.value = 'specific_profile'
    selectedProfileId.value = ''
    return
  }
  selectionMode.value = 'specific_profile'
  selectedProfileId.value = firstReady.profile.id
}

function restoreConversationModelSelection(selection) {
  const profileId = selection?.profile_id ?? ''
  const profileIsReady = readyModelProfiles.value.some((item) => item.profile.id === profileId)
  if (selection?.mode === 'specific_profile' && profileId && profileIsReady) {
    selectionMode.value = 'specific_profile'
    selectedProfileId.value = profileId
    return
  }
  if (selection?.mode === 'free_quota_first' && hasReadyFreeModel.value) {
    selectionMode.value = 'free_quota_first'
    selectedProfileId.value = ''
    return
  }
  useFreeQuotaFirstSelection()
}

function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)
}

function readinessText(value) {
  return {
    ready: '可调用', credential_required: '待配置额度 Key',
    policy_blocked: '策略已拦截', disabled: '已停用'
  }[value] ?? value
}

function costTierText(value) {
  return {
    free_quota: '免费额度', paid: '付费', local: '本地'
  }[value] ?? '费用类型未知'
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload.detail
    const error = new Error(
      detail && typeof detail === 'object'
        ? (detail.message ?? `请求失败：${response.status}`)
        : (detail ?? `请求失败：${response.status}`)
    )
    if (detail && typeof detail === 'object' && detail.code) error.code = detail.code
    throw error
  }
  return payload
}

async function requestSse(url, options, handlers) {
  const response = await fetch(url, options)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail ?? `请求失败：${response.status}`)
  }
  if (!response.body) throw new Error('浏览器未获得模型流式响应。')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const consumeFrame = (frame) => {
    const lines = frame.split(/\r?\n/)
    const eventName = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() ?? 'message'
    const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n')
    if (!data) return
    let payload
    try {
      payload = JSON.parse(data)
    } catch {
      throw new Error('模型流式响应格式异常。')
    }
    handlers[eventName]?.(payload)
  }

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      consumeFrame(buffer.slice(0, boundary))
      buffer = buffer.slice(boundary + 2)
      boundary = buffer.indexOf('\n\n')
    }
    if (done) break
  }
}

async function refreshData() {
  loading.value = true
  errorMessage.value = ''
  const historyRequest = requestJson(pageRequestUrl(historyPage.value, historyPageSize.value))
  const profileRequest = requestJson('/api/career/model-profiles')
  const candidateRequest = requestJson('/api/career/candidate-profiles')
  const catalogRequest = loadFreeModelCatalog()

  try {
    // 历史列表是首屏主内容：单独等待并立即释放 loading，不能被模型目录或简历档案拖住。
    const history = await historyRequest
    applyHistoryPage(history)
    if (selectedConversation.value) {
      const current = conversations.value.find((item) => item.id === selectedConversation.value.id)
      if (current) await selectConversation(current.id, false)
      else {
        selectedConversation.value = null
        conversationContext.value = null
        messages.value = []
      }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '求职助手数据加载失败'
  } finally {
    loading.value = false
  }

  const [profileResult, candidateResult] = await Promise.allSettled([
    profileRequest,
    candidateRequest
  ])
  if (profileResult.status === 'fulfilled') {
    modelProfiles.value = profileResult.value.items ?? []
    restoreConversationModelSelection(null)
  } else if (!errorMessage.value) {
    errorMessage.value = profileResult.reason instanceof Error
      ? profileResult.reason.message
      : '模型配置加载失败'
  }
  if (candidateResult.status === 'fulfilled') {
    candidateProfiles.value = candidateResult.value.items ?? []
  } else if (!errorMessage.value) {
    errorMessage.value = candidateResult.reason instanceof Error
      ? candidateResult.reason.message
      : '基准简历列表加载失败'
  }
  await catalogRequest
}

async function selectConversation(conversationId, clearFeedback = true) {
  if (savingJobSearch.value || submittingTurnCount.value > 0) return
  resetJobSearch()
  closeConversationActions()
  const requestId = ++conversationSelectionRequestId
  loading.value = true
  errorMessage.value = ''
  if (clearFeedback) feedback.value = ''
  stopTurnRecoveryPolling()
  stopAssessmentPolling()
  cancelContextUsageRequest()
  contextUsage.value = null
  contextUsageLoading.value = false
  resetConversationDraft()
  conversationContext.value = null
  useFreeQuotaFirstSelection()
  try {
    const payload = await requestJson(`/api/career/conversations/${conversationId}`)
    if (requestId !== conversationSelectionRequestId) return
    selectedConversation.value = payload.conversation
    turnLimit.value = normalizeTurnLimit(payload.turn_limit)
    conversationContext.value = payload.context ?? null
    messages.value = payload.messages ?? []
    restoreConversationModelSelection(payload.last_model_selection)
    lastTurn.value = payload.latest_turn ?? null
    sending.value = isActiveTurn(lastTurn.value)
    void restoreServerQueueSafely(conversationId)
  } catch (error) {
    if (requestId !== conversationSelectionRequestId) return
    errorMessage.value = error instanceof Error ? error.message : '会话读取失败'
  } finally {
    if (requestId === conversationSelectionRequestId) loading.value = false
  }
}

function conversationTitleFromText(text) {
  const title = text.trim().replace(/\s+/g, ' ')
  return title ? `${title.slice(0, 24)}${title.length > 24 ? '…' : ''}` : '新的求职咨询'
}

function conversationTitle() {
  return conversationTitleFromText(messageText.value)
}

async function createConversation({
  preserveDraft = false,
  title = conversationTitle(),
  candidateProfileId = '',
  targetRoleProfileId = ''
} = {}) {
  creating.value = true
  errorMessage.value = ''
  try {
    const conversation = await requestJson('/api/career/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title,
        candidate_profile_id: candidateProfileId || null,
        target_role_profile_id: targetRoleProfileId || null
      })
    })
    historyPage.value = 1
    historyTotal.value += 1
    conversations.value = [conversation, ...conversations.value]
      .filter((item, index, items) => items.findIndex((candidate) => candidate.id === item.id) === index)
      .slice(0, historyPageSize.value)
    void loadConversationPage(1)
    selectedConversation.value = conversation
    turnLimit.value = normalizeTurnLimit()
    conversationContext.value = conversation.context ?? null
    messages.value = []
    lastTurn.value = null
    if (!preserveDraft) {
      resetConversationDraft()
      useFreeQuotaFirstSelection()
    }
    return conversation
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '新建会话失败'
    return null
  } finally {
    creating.value = false
  }
}

async function startNewConversation() {
  if (savingJobSearch.value || submittingTurnCount.value > 0) return
  resetJobSearch()
  const conversation = await createConversation({ title: '新的求职咨询' })
  if (conversation) feedback.value = '新对话已开启，可以直接提问；简历和 JD 均可稍后补充。'
}

function openContextSetup() {
  if (!selectedConversation.value) {
    errorMessage.value = '请先开启一个对话。'
    return
  }
  contextSetupInitial.value = conversationContext.value
  showContextSetup.value = true
}

function openTargetAdjustment() {
  openContextSetup()
}

function closeContextSetup() {
  if (creating.value) return
  showContextSetup.value = false
  contextSetupInitial.value = null
}

function launchInterviewMaster() {
  const selected = modelSelectionValue.value
  const opened = openBrowserInterviewMaster({
    answerModelProfileId: selected === 'free_quota_first' ? '' : selected
  })
  feedback.value = opened ? '' : '弹窗被拦截，已改为在新标签页打开。'
}

function launchOnlineAssessment() {
  const opened = openOnlineAssessmentAssistant()
  feedback.value = opened ? '' : '弹窗被拦截，请允许本站打开窗口后重试。'
}

function openGreetingDialog() {
  if (!selectedConversation.value) {
    errorMessage.value = '请先开启一个对话。'
    return
  }
  if (!conversationContext.value?.candidate_profile) {
    errorMessage.value = '请先添加简历资料，再生成岗位招呼语。'
    return
  }
  closeContextSetup()
  resetJobSearch()
  showGreetingDialog.value = true
}

function closeGreetingDialog() {
  showGreetingDialog.value = false
  nextTick(() => greetingButton.value?.focus())
}

function resetJobSearch({ restoreFocus = false } = {}) {
  showJobSearch.value = false
  jobSearchError.value = ''
  pendingJobLibraryTarget.value = null
  if (restoreFocus) nextTick(() => jobSearchButton.value?.focus())
}

function openJobSearch() {
  if (!selectedConversation.value) {
    errorMessage.value = '请先开启一个对话。'
    return
  }
  closeContextSetup()
  jobSearchError.value = ''
  pendingJobLibraryTarget.value = null
  showJobSearch.value = true
}

function closeJobSearch() {
  if (savingJobSearch.value) return
  resetJobSearch({ restoreFocus: true })
}

function jobLibrarySelectionKey(job) {
  return String(job?.id ?? job?.jobId ?? job?.sourceUrl ?? `${job?.company ?? ''}:${job?.title ?? ''}`)
}

async function confirmJobFromLibrary(job) {
  if (!selectedConversation.value || savingJobSearch.value) return
  const conversationId = selectedConversation.value.id
  const candidateProfileId = conversationContext.value?.candidate_profile?.id ?? null
  const selectionKey = jobLibrarySelectionKey(job)
  savingJobSearch.value = true
  jobSearchError.value = ''
  try {
    let targetRole = pendingJobLibraryTarget.value?.selectionKey === selectionKey
      ? pendingJobLibraryTarget.value.targetRole
      : null
    if (!targetRole) {
      targetRole = await requestJson('/api/career/target-role-profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(toTargetRolePayload(job))
      })
      pendingJobLibraryTarget.value = { selectionKey, targetRole }
    }

    const nextContext = await requestJson(`/api/career/conversations/${conversationId}/context`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        candidate_profile_id: candidateProfileId,
        target_role_profile_id: targetRole.id
      })
    })
    if (selectedConversation.value?.id === conversationId) {
      conversationContext.value = nextContext
      feedback.value = '职位库岗位已导入，岗位分析已开始；后续回答将使用新岗位。'
    }
    resetJobSearch({ restoreFocus: true })
  } catch (error) {
    jobSearchError.value = error instanceof Error ? error.message : '职位导入失败，请稍后重试。'
  } finally {
    savingJobSearch.value = false
  }
}

function addCandidateProfile(profile) {
  candidateProfiles.value = [profile, ...candidateProfiles.value.filter((item) => item.id !== profile.id)]
}

async function applyCareerContext(payload) {
  const candidateProfileId = payload?.candidateProfile?.id ?? null
  const targetRoleProfileId = payload?.targetRole?.id ?? null
  if (!selectedConversation.value) return
  if (!candidateProfileId && !targetRoleProfileId) {
    showContextSetup.value = false
    contextSetupInitial.value = null
    feedback.value = '未添加求职资料，你仍然可以继续正常对话。'
    return
  }
  creating.value = true
  errorMessage.value = ''
  try {
    conversationContext.value = await requestJson(`/api/career/conversations/${selectedConversation.value.id}/context`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        candidate_profile_id: candidateProfileId,
        target_role_profile_id: targetRoleProfileId
      })
    })
    const updatedLabels = [
      candidateProfileId ? '简历' : '',
      targetRoleProfileId ? '目标岗位' : ''
    ].filter(Boolean)
    feedback.value = `${updatedLabels.join('和')}已更新，岗位分析已开始；后续回答将自动使用最新资料。`
    showContextSetup.value = false
    contextSetupInitial.value = null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '求职上下文建立失败'
  } finally {
    creating.value = false
  }
}

async function retryJobAssessment() {
  const conversationId = selectedConversation.value?.id
  if (!conversationId || isAssessmentPending(conversationContext.value?.assessment)) return
  errorMessage.value = ''
  try {
    conversationContext.value = await requestJson(`/api/career/conversations/${conversationId}/assessment/retry`, {
      method: 'POST'
    })
    feedback.value = '岗位分析已重新排队。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '岗位分析重新排队失败'
  }
}

function createMaterialsRequest(input) {
  const formData = new FormData()
  formData.append('text', input.text)
  formData.append('job_url', input.jobUrl || '')
  formData.append('selection_mode', input.selectionMode)
  if (input.selectionMode === 'specific_profile' && input.selectedProfileId) {
    formData.append('model_profile_id', input.selectedProfileId)
  }
  for (const experienceId of input.interviewExperienceIds) {
    formData.append('interview_experience_ids', experienceId)
  }
  for (const skillId of input.selectedSkillIds) {
    formData.append('selected_skill_ids', skillId)
  }
  formData.append('resume_file', input.resumeFile)
  return { method: 'POST', body: formData }
}

function createTemporaryMessage(input) {
  temporaryMessageSequence += 1
  const id = `local-${Date.now()}-${temporaryMessageSequence}`
  return {
    id,
    role: 'user',
    content: input.text || input.jobUrl || `已提交附件：${input.resumeFile?.name ?? '简历材料'}`,
    created_at: new Date().toISOString(),
    local_state: 'sending'
  }
}

function replaceOrAppendMessage(message, temporaryMessageId = '') {
  if (!message?.id) return
  const existingIndex = messages.value.findIndex((item) => item.id === message.id)
  const temporaryIndex = temporaryMessageId
    ? messages.value.findIndex((item) => item.id === temporaryMessageId)
    : -1
  const replacementIndex = existingIndex >= 0 ? existingIndex : temporaryIndex
  if (replacementIndex >= 0) {
    messages.value.splice(replacementIndex, 1, message)
  } else {
    messages.value.push(message)
  }
}

function markTemporaryMessageFailed(temporaryMessageId) {
  const index = messages.value.findIndex((item) => item.id === temporaryMessageId)
  if (index < 0) return
  messages.value.splice(index, 1, {
    ...messages.value[index],
    local_state: 'failed'
  })
}

function updateStreamProgress(event) {
  const key = event?.key
  if (!key) return
  const index = streamProgress.value.findIndex((item) => item.key === key)
  const progress = {
    key,
    label: event.label || '正在处理…',
    state: event.state || 'running'
  }
  if (index >= 0) streamProgress.value.splice(index, 1, progress)
  else streamProgress.value.push(progress)
}

function finalizeStreamProgress() {
  streamProgress.value = streamProgress.value.map((item) => (
    item.state === 'running' ? { ...item, state: 'completed' } : item
  ))
}

function clearComposerAfterSubmit() {
  messageText.value = ''
  jobUrl.value = ''
  showJobUrl.value = false
  clearResumeFile()
  clearInterviewMentions()
  clearSkillInvocations()
}

function restoreComposerAfterPreflightFailure(input) {
  // 仅在请求尚未开始前恢复草稿。真正已经提交到服务端的请求失败时，
  // 保留失败消息，避免用户不知情地重复提交同一份材料。
  messageText.value = input.text
  jobUrl.value = input.jobUrl
  showJobUrl.value = Boolean(input.jobUrl)
  resumeFile.value = input.resumeFile
  selectedInterviewReferences.value = input.interviewReferences
  selectedSkillInvocations.value = input.skillInvocations
}

async function sendMessage() {
  if (!selectedConversation.value) {
    errorMessage.value = '请先开启一个对话。'
    return
  }
  if (turnLimitView.value.blocked) {
    errorMessage.value = turnLimitView.value.message
    return
  }
  if (sending.value) {
    errorMessage.value = '当前回复完成后可继续发送'
    return
  }
  if (!hasReadyModel.value) {
    errorMessage.value = '尚未配置可调用模型，请先申请免费模型或添加模型连接。'
    openFreeModelDirectory()
    return
  }
  if (!messageText.value.trim()) {
    errorMessage.value = '请输入要咨询的问题。'
    return
  }
  const activeSkillInvocations = activeSelectedSkillInvocations(messageText.value)
  const input = {
    text: messageText.value.trim(),
    jobUrl: jobUrl.value.trim(),
    resumeFile: resumeFile.value,
    selectionMode: selectionMode.value,
    selectedProfileId: selectedProfileId.value,
    interviewExperienceIds: selectedInterviewReferences.value.map((item) => item.id),
    interviewReferences: [...selectedInterviewReferences.value],
    selectedSkillIds: activeSkillInvocations.map((item) => item.id),
    skillInvocations: [...activeSkillInvocations]
  }
  const temporaryMessage = createTemporaryMessage(input)
  const preparedTurn = {
    conversationId: selectedConversation.value.id,
    input,
    temporaryMessage
  }
  clearComposerAfterSubmit()

  await submitPreparedTurn(preparedTurn)
}

function syncSendingState() {
  sending.value = Boolean(
    submittingTurnCount.value > 0
    || activeObservedTurnId.value
    || pendingTurns.value.length > 0
    || isActiveTurn(lastTurn.value)
  )
}

async function submitPreparedTurn(preparedTurn) {
  const { conversationId, input, temporaryMessage } = preparedTurn
  const conversation = conversations.value.find((item) => item.id === conversationId)
    ?? (selectedConversation.value?.id === conversationId ? selectedConversation.value : null)
  if (!conversation) {
    messages.value.push({ ...temporaryMessage, local_state: 'failed' })
    errorMessage.value = '消息所属会话已不可用。'
    return
  }

  submittingTurnCount.value += 1
  syncSendingState()
  errorMessage.value = ''
  feedback.value = ''
  let temporaryMessageMounted = false
  try {
    messages.value.push(temporaryMessage)
    temporaryMessageMounted = true
    const hasMaterial = Boolean(input.resumeFile)
    const endpoint = hasMaterial
      ? `/api/career/conversations/${conversation.id}/turns-with-materials`
      : `/api/career/conversations/${conversation.id}/turns`
    const requestOptions = hasMaterial
      ? createMaterialsRequest(input)
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: input.text,
            job_url: input.jobUrl || null,
            selection_mode: input.selectionMode,
            model_profile_id: input.selectionMode === 'specific_profile' ? input.selectedProfileId || null : null,
            interview_experience_ids: input.interviewExperienceIds,
            selected_skill_ids: input.selectedSkillIds
          })
        }
    const payload = await requestJson(endpoint, requestOptions)
    turnLimit.value = normalizeTurnLimit(payload.turn_limit)
    const serverTurn = payload.turn
    const temporaryIndex = messages.value.findIndex((message) => message.id === temporaryMessage.id)
    if (temporaryIndex >= 0) {
      messages.value[temporaryIndex] = {
        ...messages.value[temporaryIndex],
        local_state: 'queued'
      }
    }
    pendingTurns.value = upsertServerTurn(pendingTurns.value, {
      ...serverTurn,
      content: temporaryMessage.content,
      temporaryMessageId: temporaryMessage.id,
      attachmentProcessing: payload.attachment_processing ?? null
    })
    lastTurn.value = serverTurn
    feedback.value = submissionFeedback(serverTurn.conversation_position)
    void observeNextServerTurn()
  } catch (error) {
    if (temporaryMessageMounted) {
      markTemporaryMessageFailed(temporaryMessage.id)
    } else {
      restoreComposerAfterPreflightFailure(input)
    }
    if (error?.code === 'conversation_turn_limit_reached') {
      turnLimit.value = normalizeTurnLimit({ ...turnLimit.value, remaining_turns: 0, reached: true })
    }
    errorMessage.value = error instanceof Error ? error.message : '消息提交失败'
  } finally {
    submittingTurnCount.value = Math.max(0, submittingTurnCount.value - 1)
    syncSendingState()
  }
}

function updateObservedTurn(turnId, update) {
  const item = pendingTurns.value.find((candidate) => candidate.id === turnId)
  if (!item) return null
  if (update.status) item.serverTurn.status = update.status
  if (update.progress) item.progress = update.progress
  if (typeof update.streamedText === 'string') item.streamedText = update.streamedText
  return item
}

function syncObservedTurnDisplay(item) {
  if (!item || activeObservedTurnId.value !== item.id) return
  streamedAssistantText.value = item.streamedText
  streamProgress.value = item.progress
  streamStatus.value = observedTurnStatus(item.serverTurn)
}

async function observeNextServerTurn() {
  if (activeObservedTurnId.value) return
  const conversationId = selectedConversation.value?.id
  if (!conversationId) return
  const item = firstServerTurn(pendingTurns.value)
  if (!item) {
    syncSendingState()
    return
  }
  const observation = turnObservationCoordinator.begin(conversationId, item.id)
  const observationIsCurrent = () => (
    turnObservationCoordinator.isCurrent(observation)
    && selectedConversation.value?.id === conversationId
  )
  activeObservedTurnId.value = item.id
  activeTemporaryMessageId.value = item.temporaryMessageId
  syncSendingState()
  syncObservedTurnDisplay(item)
  let payload = null
  let streamFailure = ''
  let terminalObserved = false
  let retryObservation = false
  try {
    await requestSse(`/api/career/turns/${item.id}/events`, {
      method: 'GET',
      signal: observation.signal
    }, {
      queued: (event) => {
        if (!observationIsCurrent()) return
        updateObservedTurn(item.id, { status: event.state ?? 'queued' })
        syncObservedTurnDisplay(item)
      },
      started: () => {
        if (!observationIsCurrent()) return
        updateObservedTurn(item.id, { status: 'running' })
        syncObservedTurnDisplay(item)
      },
      progress: (event) => {
        if (!observationIsCurrent()) return
        const current = pendingTurns.value.find((candidate) => candidate.id === item.id)
        if (!current) return
        const presentedProgress = presentTurnProgress(event)
        const progress = [
          ...current.progress.filter((entry) => entry.key !== presentedProgress.key),
          presentedProgress
        ]
        updateObservedTurn(item.id, { progress })
        syncObservedTurnDisplay(current)
      },
      delta: (event) => {
        if (!observationIsCurrent()) return
        const current = pendingTurns.value.find((candidate) => candidate.id === item.id)
        if (!current) return
        updateObservedTurn(item.id, {
          streamedText: current.streamedText + (event.content ?? '')
        })
        syncObservedTurnDisplay(current)
      },
      done: (event) => {
        if (observationIsCurrent()) {
          payload = event
          if (event.context_usage) contextUsage.value = event.context_usage
          if (event.turn_limit) turnLimit.value = normalizeTurnLimit(event.turn_limit)
        }
      },
      error: (event) => {
        if (!observationIsCurrent()) return
        if (event.assistant_message) {
          payload = event
          if (event.context_usage) contextUsage.value = event.context_usage
        }
        else streamFailure = publicTurnError(event.detail ?? event.message)
      }
    })
    if (!observationIsCurrent()) return
    if (!payload) throw new Error(streamFailure || '暂未收到完整回复。')
    terminalObserved = true
    replaceOrAppendMessage(payload.message, item.temporaryMessageId)
    replaceOrAppendMessage(payload.assistant_message)
    lastTurn.value = payload.turn ?? { ...item.serverTurn, status: payload.state }
    feedback.value = payload.turn?.status === 'succeeded'
      ? '本轮回复已完成。'
      : '本轮内容已保存，但回复未完成；具体原因已显示在对话内。'
    const jobSource = payload.job_source ?? {}
    if (jobSource.status === 'unavailable' || jobSource.status === 'not_configured') {
      feedback.value = `${feedback.value} ${jobSource.message || '职位链接暂时无法读取，请直接粘贴职位描述。'}`
    }
    historyPage.value = 1
    void loadConversationPage(1)
  } catch (error) {
    if (!observationIsCurrent() || error?.name === 'AbortError') return
    try {
      const statusPayload = await requestJson(`/api/career/turns/${item.id}`)
      if (!observationIsCurrent()) return
      const latestTurn = statusPayload.turn
      lastTurn.value = latestTurn
      if (isActiveTurn(latestTurn)) {
        pendingTurns.value = upsertServerTurn(pendingTurns.value, {
          ...latestTurn,
          content: item.content,
          temporaryMessageId: item.temporaryMessageId
        })
        retryObservation = true
        feedback.value = '回复仍在继续，正在恢复显示进度。'
      } else if (latestTurn.status === 'succeeded') {
        const conversationPayload = await requestJson(`/api/career/conversations/${conversationId}`)
        if (!observationIsCurrent()) return
        messages.value = conversationPayload.messages ?? []
        turnLimit.value = normalizeTurnLimit(conversationPayload.turn_limit)
        conversationContext.value = conversationPayload.context ?? null
        restoreConversationModelSelection(conversationPayload.last_model_selection)
        lastTurn.value = conversationPayload.latest_turn ?? latestTurn
        terminalObserved = true
        feedback.value = '本轮回复已恢复。'
      } else {
        terminalObserved = true
        markTemporaryMessageFailed(item.temporaryMessageId)
        errorMessage.value = publicTurnError(latestTurn.error_message)
      }
    } catch (statusError) {
      if (!observationIsCurrent()) return
      retryObservation = true
      errorMessage.value = publicTurnError(
        statusError instanceof Error
          ? statusError.message
          : (error instanceof Error ? error.message : '')
      )
    }
  } finally {
    if (!observationIsCurrent()) return
    if (terminalObserved) {
      pendingTurns.value = removeServerTurn(pendingTurns.value, item.id)
    }
    turnObservationCoordinator.finish(observation)
    clearObservedTurnDisplay()
    syncSendingState()
    if (retryObservation) {
      setTimeout(() => {
        if (selectedConversation.value?.id === conversationId) void observeNextServerTurn()
      }, 1000)
    } else if (pendingTurns.value.length > 0) {
      void observeNextServerTurn()
    }
  }
}

async function archiveConversation(conversation) {
  if (!conversation?.id || savingJobSearch.value || conversationActionLoadingId.value) return
  const isSelectedConversation = selectedConversation.value?.id === conversation.id
  if (isSelectedConversation) resetJobSearch()
  conversationActionLoadingId.value = conversation.id
  try {
    await requestJson(`/api/career/conversations/${conversation.id}/archive`, { method: 'POST' })
    conversations.value = conversations.value.filter((item) => item.id !== conversation.id)
    historyTotal.value = Math.max(0, historyTotal.value - 1)
    historyPage.value = Math.min(historyPage.value, historyTotalPages.value)
    closeConversationActions()
    if (isSelectedConversation) {
      selectedConversation.value = null
      conversationContext.value = null
      messages.value = []
      resetConversationDraft()
      useFreeQuotaFirstSelection()
    }
    feedback.value = '会话已归档，历史会被保留但不能继续写入。'
    void loadConversationPage(historyPage.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '会话归档失败'
  } finally {
    conversationActionLoadingId.value = ''
  }
}

function toggleConversationMenu(conversationId) {
  if (sending.value || conversationActionLoadingId.value) return
  if (conversationMenuId.value === conversationId) {
    closeConversationActions()
    return
  }
  conversationMenuId.value = conversationId
  renamingConversationId.value = ''
  renameTitleDraft.value = ''
  deletingConversationId.value = ''
}

function startConversationRename(conversation) {
  deletingConversationId.value = ''
  renamingConversationId.value = conversation.id
  renameTitleDraft.value = conversation.title
}

async function saveConversationRename(conversation) {
  const title = renameTitleDraft.value.trim()
  if (!title) {
    errorMessage.value = '会话名称不能为空'
    return
  }
  if (title === conversation.title) {
    closeConversationActions()
    return
  }

  conversationActionLoadingId.value = conversation.id
  errorMessage.value = ''
  try {
    const updatedConversation = await requestJson(`/api/career/conversations/${conversation.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title })
    })
    conversations.value = conversations.value.map((item) => (
      item.id === conversation.id ? updatedConversation : item
    ))
    if (selectedConversation.value?.id === conversation.id) {
      selectedConversation.value = { ...selectedConversation.value, ...updatedConversation }
    }
    feedback.value = '会话名称已更新。'
    closeConversationActions()
    historyPage.value = 1
    void loadConversationPage(1)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '会话重命名失败'
  } finally {
    conversationActionLoadingId.value = ''
  }
}

function startConversationDelete(conversationId) {
  renamingConversationId.value = ''
  renameTitleDraft.value = ''
  deletingConversationId.value = conversationId
}

async function deleteConversation(conversation) {
  if (conversationActionLoadingId.value || savingJobSearch.value) return
  resetJobSearch()
  conversationActionLoadingId.value = conversation.id
  errorMessage.value = ''
  try {
    await requestJson(`/api/career/conversations/${conversation.id}`, { method: 'DELETE' })
    conversations.value = conversations.value.filter((item) => item.id !== conversation.id)
    historyTotal.value = Math.max(0, historyTotal.value - 1)
    historyPage.value = Math.min(historyPage.value, historyTotalPages.value)
    if (selectedConversation.value?.id === conversation.id) {
      conversationSelectionRequestId += 1
      stopTurnRecoveryPolling()
      selectedConversation.value = null
      conversationContext.value = null
      messages.value = []
      lastTurn.value = null
      resetConversationDraft()
      useFreeQuotaFirstSelection()
    }
    feedback.value = '会话及其历史记录已永久删除。'
    closeConversationActions()
    void loadConversationPage(historyPage.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '会话删除失败'
  } finally {
    conversationActionLoadingId.value = ''
  }
}

function chooseModel(event) {
  const value = event.target.value
  if (value === 'free_quota_first') {
    useFreeQuotaFirstSelection()
    return
  }
  const selected = readyModelProfiles.value.find((item) => item.profile.id === value)
  if (!selected) {
    useFirstReadyModelSelection()
    errorMessage.value = '该模型连接当前不可调用，请在“模型与连接”中重新测试或配置。'
    return
  }
  selectionMode.value = 'specific_profile'
  selectedProfileId.value = selected.profile.id
}

async function saveModelProfile() {
  const form = modelForm.value
  const validationMessage = connectionValidationMessage()
  if (validationMessage) {
    connectionSaveError.value = validationMessage
    return
  }
  if (testedConnectionFingerprint.value !== connectionFingerprint()) {
    connectionTestError.value = '请先对当前配置执行“测试连接”，确认可用后再保存。'
    return
  }
  savingModel.value = true
  connectionSaveError.value = ''
  try {
    const payload = await requestJson(`/api/career/model-connections/${encodeURIComponent(resolvedProfileKey())}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(modelConnectionPayload())
    })
    const index = modelProfiles.value.findIndex((item) => item.profile.id === payload.profile.id)
    if (index >= 0) modelProfiles.value.splice(index, 1, payload)
    else modelProfiles.value = [...modelProfiles.value, payload]
    // 免费目录是辅助入口；刷新失败不能回滚已验证并保存的模型连接。
    await loadFreeModelCatalog()
    if (payload.readiness === 'ready') {
      selectionMode.value = 'specific_profile'
      selectedProfileId.value = payload.profile.id
    } else {
      useFreeQuotaFirstSelection()
    }
    contextUsageRevision.value += 1
    modelForm.value = emptyModelForm()
    modelDialogMode.value = 'list'
    connectionTestMessage.value = ''
    testedConnectionFingerprint.value = ''
    feedback.value = '模型连接已通过测试并保存。API Key 已保存到本机 PostgreSQL 模型连接表。'
  } catch (error) {
    connectionSaveError.value = connectionErrorText(error, '保存失败，请检查配置后重试。')
  } finally {
    savingModel.value = false
  }
}

function modelConnectionPayload() {
  const form = modelForm.value
  const capabilities = []
  if (form.text) capabilities.push('text')
  if (form.vision) capabilities.push('vision')
  if (form.tools) capabilities.push('tools')
  const apiBaseUrl = providerApiBaseUrl(form.providerKey, form.apiBaseUrl)
  const websiteUrl = providerWebsiteUrl(form.providerKey, form.websiteUrl)
  return {
    display_name: resolvedDisplayName(),
    provider_key: form.providerKey.trim(),
    model_id: form.modelId.trim(),
    api_key: form.apiKey.trim(),
    capabilities,
    cost_tier: form.costTier,
    priority: Number(form.priority),
    enabled: true,
    api_base_url: apiBaseUrl || null,
    provider_website_url: websiteUrl || null
  }
}

function resolvedDisplayName() {
  const configuredName = modelForm.value.displayName.trim()
  if (configuredName) return configuredName
  return `${activeProviderOption.value.label} 模型连接`
}

function resolvedProfileKey() {
  const configuredKey = modelForm.value.profileKey.trim()
  if (configuredKey) return configuredKey
  const providerKey = activeProviderOption.value.key || 'model'
  const modelKey = modelForm.value.modelId
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)
  return `${providerKey}-${modelKey || 'connection'}`.slice(0, 64)
}

function providerApiBaseUrl(providerKey, configuredUrl) {
  const provider = providerOptions.find((item) => item.key === providerKey)
  if (provider && provider.key !== 'custom') return provider.apiBaseUrl
  return String(configuredUrl ?? '').trim()
}

function providerWebsiteUrl(providerKey, configuredUrl) {
  const provider = providerOptions.find((item) => item.key === providerKey)
  if (provider && provider.key !== 'custom') return provider.websiteUrl
  return String(configuredUrl ?? '').trim()
}

function connectionFingerprint() {
  const payload = modelConnectionPayload()
  return JSON.stringify(payload)
}

function invalidateConnectionTest() {
  testedConnectionFingerprint.value = ''
  connectionTestMessage.value = ''
  connectionTestError.value = ''
  connectionSaveError.value = ''
}

function connectionErrorText(error, fallback) {
  const message = error instanceof Error ? error.message.trim() : ''
  if (!message) return fallback
  if (/failed to fetch|networkerror|load failed/i.test(message)) {
    return '无法连接到本地求职助手服务。请确认后台服务正在运行，然后重试。'
  }
  return message.replace(/^模型连接测试未通过：\s*/, '')
}

function apiBaseUrlValidationMessage() {
  const apiBaseUrl = providerApiBaseUrl(modelForm.value.providerKey, modelForm.value.apiBaseUrl)
  try {
    const url = new URL(apiBaseUrl)
    if (url.protocol !== 'https:') {
      return 'API Base URL 必须以 https:// 开头。'
    }
  } catch {
    return 'API Base URL 格式不正确，请填写完整的 HTTPS 地址。'
  }
  return ''
}

function connectionValidationMessage() {
  const form = modelForm.value
  if (!form.modelId.trim()) return '请填写模型名称（Model ID）。'
  if (!form.apiKey.trim()) return '请粘贴该服务商的 API Key。'
  if (!form.text && !form.vision && !form.tools) return '请至少选择一种模型能力。'
  if (!usesPresetEndpoint.value && !form.apiBaseUrl.trim()) {
    return '自定义兼容服务商需要填写请求地址（API Base URL）。'
  }
  return apiBaseUrlValidationMessage()
}

async function testModelConnection() {
  const validationMessage = connectionValidationMessage()
  if (validationMessage) {
    connectionTestError.value = validationMessage
    return
  }
  testingConnection.value = true
  connectionTestMessage.value = ''
  connectionTestError.value = ''
  try {
    const result = await requestJson('/api/career/model-connections/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(modelConnectionPayload())
    })
    testedConnectionFingerprint.value = connectionFingerprint()
    const responsePreview = typeof result.response_preview === 'string' ? result.response_preview.trim() : ''
    connectionTestMessage.value = responsePreview
      ? `连接测试通过：模型已回复“${responsePreview}”。`
      : '连接测试通过：地址、模型 ID 与 API Key 均可用。'
    connectionSaveError.value = ''
  } catch (error) {
    testedConnectionFingerprint.value = ''
    connectionTestError.value = connectionErrorText(error, '模型连接测试失败，请检查配置后重试。')
  } finally {
    testingConnection.value = false
  }
}

onMounted(() => {
  historyCollapsed.value = window.localStorage.getItem(HISTORY_COLLAPSED_STORAGE_KEY) === '1'
  historyPageSize.value = resolveHistoryPageSize(
    window.localStorage.getItem(HISTORY_PAGE_SIZE_STORAGE_KEY),
    window.innerHeight
  )
  const storedContextRailWidth = Number(window.localStorage.getItem(CONTEXT_RAIL_WIDTH_STORAGE_KEY))
  if (Number.isFinite(storedContextRailWidth) && storedContextRailWidth > 0) {
    contextRailWidth.value = clampContextRailWidth(storedContextRailWidth)
  }
  const workspaceElement = careerWorkspaceRef.value
  if (workspaceElement && typeof ResizeObserver !== 'undefined') {
    careerResizeObserver = new ResizeObserver(([entry]) => {
      syncCareerWorkspaceWidth(entry?.contentRect?.width ?? workspaceElement.clientWidth)
    })
    careerResizeObserver.observe(workspaceElement)
    syncCareerWorkspaceWidth(workspaceElement.clientWidth)
  }
  document.addEventListener('keydown', handleCareerPanelKeydown)
  document.addEventListener('pointerdown', handleConversationActionPointerDown)
  void refreshData()
})
</script>

<template>
  <section
    ref="careerWorkspaceRef"
    class="career-workspace"
    :class="{
      'has-context-rail': conversationContext,
      'history-collapsed': historyCollapsed,
      'resizing-context-rail': resizingContextRail,
      'compact-history-open': compactPanel === 'history',
      'compact-context-open': compactPanel === 'context'
    }"
    :style="{ '--context-rail-width': `${visibleContextRailWidth}px` }"
    aria-label="求职助手"
  >
    <aside id="career-history-panel" class="career-history-panel" :class="{ collapsed: historyCollapsed }">
      <button
        ref="historyDrawerClose"
        class="compact-panel-close"
        type="button"
        aria-label="关闭会话历史"
        @click="closeCompactPanel"
      >×</button>
      <template v-if="historyCollapsed && layoutMode !== 'single'">
        <button class="history-rail-button" type="button" aria-label="展开会话历史" title="展开会话历史" @click="toggleHistoryPanel">›</button>
        <button class="history-rail-button primary" type="button" :disabled="creating || submittingTurnCount > 0 || savingJobSearch" aria-label="开启新对话" title="开启新对话" @click="startNewConversation">＋</button>
        <span class="history-rail-count" :title="`${historyTotal} 个会话`">{{ historyTotal }}</span>
      </template>
      <template v-else>
        <div class="history-actions">
          <button class="primary-button" type="button" :disabled="creating || submittingTurnCount > 0 || savingJobSearch" @click="startNewConversation">
            ＋ {{ creating ? '正在建立...' : '开启新对话' }}
          </button>
          <button class="history-collapse-button" type="button" aria-label="折叠会话历史" title="折叠会话历史" @click="toggleHistoryPanel">‹</button>
        </div>
        <div class="history-title"><span>历史对话</span><small>{{ historyTotal }}</small></div>
        <p v-if="(loading || historyLoading) && !conversations.length" class="list-empty">正在加载会话...</p>
        <p v-else-if="!conversations.length" class="list-empty">还没有求职会话</p>
        <div v-else class="conversation-list" :class="{ 'is-loading': historyLoading }">
        <article
          v-for="conversation in conversations"
          :key="conversation.id"
          class="conversation-item"
          :class="{ active: selectedConversation?.id === conversation.id, managing: conversationMenuId === conversation.id }"
        >
          <button
            type="button"
            class="conversation-item-main"
            :disabled="submittingTurnCount > 0 || savingJobSearch || conversationActionLoadingId === conversation.id"
            @click="selectConversation(conversation.id)"
          >
            <strong>{{ conversation.title }}</strong>
            <small>{{ formatDate(conversation.updated_at) }}</small>
          </button>
          <button
            type="button"
            class="conversation-menu-button"
            :disabled="sending || Boolean(conversationActionLoadingId)"
            :aria-expanded="conversationMenuId === conversation.id"
            :aria-label="`管理会话：${conversation.title}`"
            @click.stop="toggleConversationMenu(conversation.id)"
          >
            ···
          </button>

          <div v-if="conversationMenuId === conversation.id" class="conversation-action-menu" @click.stop>
            <template v-if="renamingConversationId === conversation.id">
              <label>
                <span>会话名称</span>
                <input
                  v-model="renameTitleDraft"
                  type="text"
                  maxlength="160"
                  autocomplete="off"
                  @keyup.enter="saveConversationRename(conversation)"
                  @keyup.esc="closeConversationActions"
                />
              </label>
              <div class="conversation-action-buttons">
                <button type="button" @click="closeConversationActions">取消</button>
                <button type="button" class="primary" :disabled="conversationActionLoadingId === conversation.id" @click="saveConversationRename(conversation)">
                  {{ conversationActionLoadingId === conversation.id ? '保存中…' : '保存名称' }}
                </button>
              </div>
            </template>

            <template v-else-if="deletingConversationId === conversation.id">
              <p><strong>永久删除该会话？</strong><span>会话及其历史记录将被永久删除，此操作无法撤销。</span></p>
              <div class="conversation-action-buttons">
                <button type="button" @click="closeConversationActions">取消</button>
                <button type="button" class="danger" :disabled="conversationActionLoadingId === conversation.id" @click="deleteConversation(conversation)">
                  {{ conversationActionLoadingId === conversation.id ? '删除中…' : '永久删除' }}
                </button>
              </div>
            </template>

            <div v-else class="conversation-action-options">
              <button type="button" @click="startConversationRename(conversation)">重命名</button>
              <button type="button" :disabled="conversationActionLoadingId === conversation.id" @click="archiveConversation(conversation)">
                {{ conversationActionLoadingId === conversation.id ? '归档中…' : '归档' }}
              </button>
              <button type="button" class="danger" @click="startConversationDelete(conversation.id)">删除会话</button>
            </div>
          </div>
        </article>
        </div>
        <nav v-if="historyTotal" class="history-pagination-shell" aria-label="会话历史分页">
          <ElConfigProvider :locale="zhCn">
            <ElPagination
              class="history-pagination"
              background
              small
              :current-page="historyPage"
              :page-size="historyPageSize"
              :page-sizes="HISTORY_PAGE_SIZE_OPTIONS"
              :pager-count="5"
              :total="historyTotal"
              :disabled="historyLoading"
              layout="total, sizes, prev, pager, next"
              @current-change="goToHistoryPage"
              @size-change="changeHistoryPageSize"
            />
          </ElConfigProvider>
        </nav>
        <div class="privacy-note"><span aria-hidden="true">盾</span><p>简历原文件不入库，仅保留配置允许的历史文本</p></div>
      </template>
    </aside>

    <section class="career-chat-panel">
      <header class="chat-header">
        <div>
          <h2>{{ selectedConversation?.title || '求职助手' }}</h2>
          <p v-if="conversationContext" class="active-context-line">
            <span v-if="conversationContext.candidate_profile">简历 v{{ conversationContext.candidate_profile.version }}</span>
            <i v-if="conversationContext.candidate_profile && conversationContext.target_role">→</i>
            <span v-if="conversationContext.target_role">{{ conversationContext.target_role.company_name }} · {{ conversationContext.target_role.role_name }} v{{ conversationContext.target_role.version }}</span>
          </p>
        </div>
        <div class="compact-panel-triggers" aria-label="辅助面板">
          <button
            ref="historyDrawerTrigger"
            type="button"
            class="compact-panel-trigger history-trigger"
            aria-controls="career-history-panel"
            :aria-expanded="compactPanel === 'history'"
            @click="toggleCompactPanel('history')"
          >历史</button>
          <button
            v-if="conversationContext"
            ref="contextDrawerTrigger"
            type="button"
            class="compact-panel-trigger context-trigger"
            aria-controls="career-context-panel"
            :aria-expanded="compactPanel === 'context'"
            @click="toggleCompactPanel('context')"
          >职位信息</button>
        </div>
        <div v-if="selectedConversation" class="chat-material-actions" aria-label="求职资料快捷操作">
          <button
            v-if="onlineAssessmentVisible"
            type="button"
            class="chat-material-button online-assessment"
            title="从当前在线笔试题目页识别题面，生成代码并执行可见测试"
            @click="launchOnlineAssessment"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 7-4 5 4 5M16 7l4 5-4 5M14 4l-4 16"/></svg>
            <span>线上笔试</span>
          </button>
          <button
            v-if="interviewMasterVisible"
            type="button"
            class="chat-material-button interview-master"
            title="选择面试标签页音频，实时识别问题并生成中文回答建议"
            @click="launchInterviewMaster"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 13a8 8 0 0 1 16 0"/><path d="M4 13v5h3v-5zM17 13v5h3v-5z"/><path d="M9 19h6"/></svg>
            <span>面试大师</span>
          </button>
          <button
            ref="greetingButton"
            type="button"
            class="chat-material-button greeting"
            title="从职位库批量生成并预览岗位招呼语"
            :disabled="savingJobSearch"
            @click="openGreetingDialog"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5h16v10H9l-5 4z"/><path d="M8 9.5h8M8 12.5h5"/></svg>
            <span>一键打招呼</span>
          </button>
          <button
            ref="jobSearchButton"
            type="button"
            class="chat-material-button primary"
            :aria-label="hasTargetContext ? '职位检索，更换当前岗位' : '职位检索，从职位库选择'"
            :title="hasTargetContext ? '从职位库更换当前岗位' : '从职位库选择目标岗位'"
            :disabled="savingJobSearch"
            @click="openJobSearch"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4.5 4.5"/></svg>
            <span>职位检索</span>
          </button>
        </div>
      </header>

      <section v-if="feedback" class="notice success">{{ feedback }}</section>

      <section v-if="!selectedConversation" class="empty-state">
        <div class="empty-mark">职</div><h2>开启一段求职对话</h2>
        <p>无需先上传简历或 JD。开启后可以直接提问，也可以在对话中随时补充资料。</p>
        <button class="empty-context-button" type="button" @click="startNewConversation">直接开启对话</button>
      </section>

      <section v-else ref="messageListRef" class="message-list" aria-live="polite">
        <article v-if="!messages.length" class="agent-message">
          <header class="message-role"><i aria-hidden="true">职</i><strong>求职助手</strong></header>
          <CareerMessageContent :content="welcomeMessage" />
        </article>
        <article v-for="message in messages" :key="message.id" class="message" :class="[message.role === 'user' ? 'from-user' : 'from-agent', message.local_state ? `message-${message.local_state}` : '']" :aria-label="message.role === 'user' ? '你的消息' : '求职助手消息'">
          <header v-if="message.role !== 'user'" class="message-role">
            <i aria-hidden="true">职</i><strong>求职助手</strong>
            <span v-if="messageModelLabel(message)" class="message-model-label" title="本条回复的实际调用模型">{{ messageModelLabel(message) }}</span>
          </header>
          <CareerMessageContent :content="message.content" />
          <small class="message-time">{{ formatDate(message.created_at) }}<template v-if="message.local_state === 'sending'"> · 正在发送</template><template v-else-if="message.local_state === 'queued'"> · 等待回复</template><template v-else-if="message.local_state === 'failed'"> · 发送失败</template></small>
        </article>
        <article v-if="sending" class="agent-message agent-pending">
          <div class="stream-pending-heading"><div class="message-role"><i aria-hidden="true">职</i><strong>求职助手</strong></div><span>正在思考</span></div>
          <ol v-if="streamProgress.length" class="stream-progress-list" aria-label="处理进展">
            <li v-for="progress in streamProgress" :key="progress.key" class="stream-progress-item" :class="progress.state"><i></i><span>{{ progress.label }}</span></li>
          </ol>
          <CareerMessageContent v-if="streamedAssistantText" class="streamed-answer" :content="streamedAssistantText" />
          <p v-else class="stream-status">{{ streamStatus || '正在思考…' }}</p>
          <span ref="latestMessageRef" class="message-scroll-anchor" aria-hidden="true"></span>
        </article>
        <section v-if="pendingTurns.length" class="turn-queue-preview" aria-label="待回复消息">
          <strong>待回复消息（{{ pendingTurns.length }}）</strong>
          <article v-for="(pendingTurn, index) in pendingTurns" :key="pendingTurn.id">
            <span>{{ Number(pendingTurn.serverTurn.conversation_position ?? index) + 1 }}</span>
            <p>{{ pendingTurn.content }}</p>
            <small>{{ pendingTurn.serverTurn.status === 'running' ? '处理中' : '等待中' }}</small>
          </article>
        </section>
        <div v-if="lastTurn" class="turn-status" :class="{ active: isActiveTurn(lastTurn) }">{{ turnStatusText(lastTurn) }}</div>
      </section>

      <footer class="composer">
        <div class="composer-toolbar" aria-label="会话与材料工具">
          <div class="input-tools">
            <button v-if="selectedConversation" class="chip-button active-context-chip" :class="{ active: conversationContext }" type="button" @click="openContextSetup">{{ contextButtonLabel }}</button>
            <select class="model-select" :value="modelSelectionValue" :disabled="!hasReadyModel" aria-label="本轮模型选择" @change="chooseModel">
              <option v-if="!hasReadyModel" value="">尚未配置可用模型</option>
              <option v-if="hasReadyFreeModel" value="free_quota_first">【免费】自动选择</option>
              <optgroup v-if="readyFreeModelProfiles.length" label="已接入的免费模型">
                <option v-for="item in readyFreeModelProfiles" :key="item.profile.id" :value="item.profile.id">【免费】{{ modelChoiceLabel(item) }}</option>
              </optgroup>
              <optgroup v-if="readyPaidModelProfiles.length" label="已接入的付费模型">
                <option v-for="item in readyPaidModelProfiles" :key="item.profile.id" :value="item.profile.id">【付费】{{ modelChoiceLabel(item) }}</option>
              </optgroup>
              <optgroup v-if="readyLocalModelProfiles.length" label="已接入的本地模型">
                <option v-for="item in readyLocalModelProfiles" :key="item.profile.id" :value="item.profile.id">【本地】{{ modelChoiceLabel(item) }}</option>
              </optgroup>
            </select>
            <button class="chip-button free-model-entry-button" type="button" @click="openFreeModelDirectory">{{ hasReadyModel ? '免费模型' : '配置模型' }}</button>
          </div>
          <div class="session-tools">
            <button class="quiet-button model-manager-button" type="button" @click="openModelDialog">模型连接</button>
          </div>
        </div>
        <div class="composer-input-shell">
          <div ref="composerHighlightLayer" class="composer-highlight-layer" aria-hidden="true">
            <template v-for="(segment, index) in composerHighlightSegments" :key="`${index}-${segment.kind}`">
              <mark v-if="segment.kind !== 'plain'" :class="`composer-highlight-${segment.kind}`">{{ segment.text }}</mark>
              <span v-else>{{ segment.text }}</span>
            </template>
            <br v-if="composerDisplayText.endsWith('\n')" />
          </div>
          <textarea v-model="messageText" aria-label="求职咨询内容" :disabled="!selectedConversation || turnInputBlocked" :placeholder="composerPlaceholder" rows="3" @input="syncComposerHighlightInput" @scroll="syncComposerHighlightScroll" @keydown.enter.exact.prevent="sendMessage" />
        </div>
        <div v-if="skillMentionResults.length" class="interview-mention-menu skill-invocation-menu" role="listbox" aria-label="Skill 候选">
          <p class="suggestion-menu-label">技能库 · 选择后将 SKILL.md 挂载到本轮模型上下文</p>
          <button v-for="skill in skillMentionResults" :key="skill.id" type="button" role="option" @mousedown.prevent="selectSkillMention(skill)">
            <strong>✦ {{ skill.name }}</strong>
            <small>{{ skill.description_zh || skill.description }} · {{ skill.source_label }}</small>
          </button>
        </div>
        <div v-if="interviewMentionResults.length" class="interview-mention-menu" role="listbox" aria-label="面经库检索结果">
          <button v-for="experience in interviewMentionResults" :key="experience.id" type="button" role="option" @mousedown.prevent="selectInterviewMention(experience)">
            <strong>{{ experience.company_name }} · {{ experience.role_name }}</strong>
            <small>{{ experience.job_name }}<template v-if="experience.interview_date"> · {{ experience.interview_date }}</template></small>
          </button>
        </div>
        <div v-if="selectedConversation && (turnLimitView.message || sending)" class="turn-limit-notice" :class="{ blocked: turnLimitView.blocked }" role="status">
          <span>{{ sending ? '当前回复完成后可继续发送' : turnLimitView.message }}</span>
          <button v-if="turnLimitView.blocked" class="quiet-button" type="button" @click="startNewConversation">开启新对话</button>
        </div>
        <div class="composer-footer">
          <small>当前模型：{{ modelLabel }}。输入 @ 可引用面经，输入 / 可调用 Skill；每条消息都会保存，并按发送顺序回复。</small>
          <div class="composer-submit-tools">
            <CareerContextMeter :usage="contextUsage" :loading="contextUsageLoading" class="composer-context-meter" />
            <button class="send-button" type="button" :disabled="!selectedConversation || turnInputBlocked" @click="sendMessage">{{ sending ? '回复中' : '发送' }}</button>
          </div>
        </div>
      </footer>
    </section>

    <div v-if="conversationContext" id="career-context-panel" class="context-rail-slot">
      <button
        ref="contextDrawerClose"
        class="compact-panel-close context-panel-close"
        type="button"
        aria-label="关闭职位信息"
        @click="closeCompactPanel"
      >×</button>
      <button
        class="context-rail-resizer"
        type="button"
        role="separator"
        aria-label="调整职位信息栏宽度"
        aria-orientation="vertical"
        :aria-valuemin="CONTEXT_RAIL_MIN_WIDTH"
        :aria-valuemax="CONTEXT_RAIL_MAX_WIDTH"
        :aria-valuenow="contextRailWidth"
        title="拖拽调整宽度，双击恢复推荐宽度"
        @pointerdown="startContextRailResize"
        @dblclick="resetContextRailWidth"
        @keydown.left.prevent="resizeContextRailBy(24)"
        @keydown.right.prevent="resizeContextRailBy(-24)"
        @keydown.home.prevent="resetContextRailWidth"
      ><i></i></button>
      <CareerContextRail
        :context="conversationContext"
        :selected-conversation="selectedConversation"
        :wide="visibleContextRailWidth >= 580"
        @adjust="openTargetAdjustment"
        @setup="openContextSetup"
        @retry-assessment="retryJobAssessment"
      />
    </div>
    <button
      v-if="compactPanel"
      class="career-panel-scrim"
      type="button"
      aria-label="关闭辅助面板"
      @click="closeCompactPanel"
    ></button>
  </section>

  <CareerContextSetupDialog
    :open="showContextSetup"
    :busy="creating"
    :candidate-profiles="candidateProfiles"
    :initial-context="contextSetupInitial"
    @cancel="closeContextSetup"
    @ready="applyCareerContext"
    @profile-created="addCandidateProfile"
  />

  <CareerJobSearchDialog
    :open="showJobSearch"
    :busy="savingJobSearch"
    :error="jobSearchError"
    :current-target-role="conversationContext?.target_role ?? null"
    @cancel="closeJobSearch"
    @confirm="confirmJobFromLibrary"
  />

  <CareerGreetingDialog
    :open="showGreetingDialog"
    :candidate-profile="conversationContext?.candidate_profile ?? null"
    @cancel="closeGreetingDialog"
  />

  <Teleport to="body">
    <section v-if="errorMessage" class="career-error-toast" role="alert" aria-live="assertive">
      <div><strong>操作未完成</strong><p>{{ errorMessage }}</p></div>
      <button class="toast-close-button" type="button" aria-label="关闭错误提示" @click="dismissError">×</button>
    </section>
  </Teleport>

  <Teleport to="body">
    <div v-if="showModelDialog" class="model-dialog-backdrop" @mousedown.self="closeModelDialog">
      <section class="model-dialog" role="dialog" aria-modal="true" aria-label="模型与连接管理">
        <header class="model-dialog-header">
          <div>
            <h2>{{ modelDialogTitle }}</h2>
          </div>
          <button class="dialog-close-button" type="button" aria-label="关闭模型连接管理" @click="closeModelDialog">×</button>
        </header>

        <main v-if="modelDialogMode === 'list'" class="model-dialog-body">
          <div class="connection-toolbar">
            <div><strong>模型连接</strong><small>平台已托管的免费模型可直接供访客使用，不会向浏览器暴露 API Key。</small></div>
            <div class="connection-toolbar-actions">
              <button class="dialog-secondary-button" type="button" @click="openFreeModelDirectory">申请免费模型</button>
              <button class="dialog-primary-button" type="button" @click="createModelConnection">＋ 添加模型</button>
            </div>
          </div>
          <div v-if="modelProfiles.length" class="connection-card-list">
            <button v-for="item in modelProfiles" :key="item.profile.id" class="connection-card" type="button" :title="item.blocked_reason || ''" @click="editModelConnection(item)">
              <span class="connection-drag">⠿</span>
              <span class="provider-avatar">{{ item.profile.provider_key.slice(0, 2).toUpperCase() }}</span>
              <span class="connection-card-copy"><strong>{{ modelPrimaryLabel(item) }}</strong><small v-if="modelSecondaryLabel(item)">自定义名称：{{ modelSecondaryLabel(item) }}</small></span>
              <span class="connection-meta"><span :class="`readiness ${item.readiness}`">{{ readinessText(item.readiness) }}</span><small>{{ costTierText(item.profile.cost_tier) }} · 顺序 {{ item.profile.priority }}</small></span>
            </button>
          </div>
          <div v-else class="connection-empty-state"><span>⌁</span><strong>还没有可用模型</strong><p>添加一个带免费额度的服务商连接后，即可开始求职分析。</p></div>
        </main>

        <main v-else-if="modelDialogMode === 'free'" class="model-dialog-body free-model-directory">
          <button class="back-button" type="button" @click="modelDialogMode = 'list'">← 返回模型连接</button>
          <div v-if="freeModelCatalogError" class="connection-test-error catalog-load-error" role="alert">
            <div><strong>免费模型目录暂不可用</strong><span>{{ freeModelCatalogError }}</span></div>
            <button class="dialog-secondary-button" type="button" :disabled="loadingFreeModelCatalog" @click="loadFreeModelCatalog">{{ loadingFreeModelCatalog ? '正在重试…' : '重新加载目录' }}</button>
          </div>
          <div class="free-directory-intro">
            <div>
              <h3>先申请 Key，再把模型接入工作台</h3>
              <p>这里仅列出有免费层、免费额度或免费路由的官方服务。额度会随平台政策变化，实际以申请页面为准。</p>
            </div>
            <strong>{{ freeModelCatalog.length }} 个服务商</strong>
          </div>
          <div v-if="loadingFreeModelCatalog" class="connection-empty-state"><span>⌁</span><strong>正在读取免费模型目录</strong><p>正在同步服务商、申请入口与最新费用说明。</p></div>
          <div v-else-if="freeModelCatalog.length" class="free-provider-grid">
            <article v-for="offer in freeModelCatalog" :key="offer.provider_key" class="free-provider-card">
              <header>
                <span class="provider-avatar large">{{ providerOption(offer.provider_key).short }}</span>
                <div><strong>{{ offer.display_name }}</strong><small>{{ offer.free_label }}</small></div>
                <span class="catalog-readiness" :class="{ ready: offer.platform_ready }">{{ offer.platform_ready ? '已有可用模型' : '未配置 Key' }}</span>
              </header>
              <p>{{ offer.free_description }}</p>
              <div class="catalog-access-summary" :class="{ ready: offer.platform_ready }">{{ offer.availability_label }}</div>
              <div class="free-model-template-list">
                <div v-for="model in offer.models" :key="model.model_id" class="free-model-template">
                  <div><strong>{{ model.display_name }}</strong><code>{{ model.model_id }}</code></div>
                  <div class="free-model-template-meta">
                    <small>{{ catalogModelStatus(offer, model) }}</small>
                    <a
                      :href="offer.setup_url"
                      target="_blank"
                      rel="noopener noreferrer"
                      :aria-label="`打开 ${offer.display_name} 的 API Key 申请页面`"
                    >获取 API Key ↗</a>
                  </div>
                  <button type="button" @click="configureCatalogModel(offer, model)">{{ configuredCatalogProfile(offer, model) ? '编辑连接' : '填写 Key 并测试' }}</button>
                </div>
              </div>
              <footer>
                <a :href="offer.setup_url" target="_blank" rel="noopener noreferrer">申请 API Key ↗</a>
                <a :href="offer.documentation_url" target="_blank" rel="noopener noreferrer">官方接入文档 ↗</a>
                <a :href="offer.pricing_url || offer.documentation_url" target="_blank" rel="noopener noreferrer">费用与免费额度 ↗</a>
              </footer>
            </article>
          </div>
          <div v-else-if="!freeModelCatalogError" class="connection-empty-state"><span>⌁</span><strong>暂无免费模型目录</strong><p>仍可返回“模型与连接”，手动添加 OpenAI-compatible 模型服务。</p></div>
        </main>

        <main v-else-if="modelDialogMode === 'setup'" class="model-dialog-body connection-setup-body">
          <button class="back-button" type="button" @click="modelDialogMode = modelSetupReturnMode">← {{ modelSetupReturnMode === 'free' ? '返回免费模型目录' : '返回已配置模型' }}</button>
          <section class="provider-catalog-section" aria-labelledby="provider-catalog-title">
            <div><h3 id="provider-catalog-title">1. 选择服务商</h3></div>
            <div class="provider-picker-grid provider-picker-grid-expanded">
              <button v-for="provider in providerOptions" :key="provider.key" class="provider-picker-card" :class="{ selected: activeProviderOption.key === provider.key }" type="button" @click="chooseProvider(provider)">
                <span class="provider-avatar large">{{ provider.short }}</span><span><strong>{{ provider.label }}</strong><small>{{ provider.detail }}</small></span><em v-if="provider.key !== 'custom'">预置地址</em><em v-else>手动配置</em>
              </button>
            </div>
          </section>

          <section ref="connectionConfigRef" class="connection-config-section" aria-labelledby="connection-config-title">
            <div class="connection-section-heading"><div><h3 id="connection-config-title">2. 填写连接配置</h3></div></div>
            <div class="connection-form-grid">
              <label v-if="!usesPresetEndpoint">连接名称<span>只在本平台展示，例如“自定义求职分析”</span><input v-model="modelForm.displayName" placeholder="例如：自定义求职分析" @input="invalidateConnectionTest" /></label>
              <label>模型名称（Model ID）<span>从服务商控制台复制模型或接入点标识</span><input v-model="modelForm.modelId" :placeholder="activeProviderOption.modelHint" @input="invalidateConnectionTest" /></label>
              <label v-if="!usesPresetEndpoint">官网地址<span>用于打开服务商控制台，不参与模型调用</span><input v-model="modelForm.websiteUrl" placeholder="https://platform.example.com" @input="invalidateConnectionTest" /></label>
              <label v-if="!usesPresetEndpoint">免费模型候选顺序<span>数值越小，自动选择时越靠前</span><input v-model.number="modelForm.priority" min="0" max="10000" type="number" @input="invalidateConnectionTest" /></label>
              <div class="full-width api-key-field"><label for="career-model-api-key">API Key<span>显式填写；测试和保存后均不会回显。生产环境请通过 HTTPS 访问本平台。</span></label><span class="api-key-input-shell"><input id="career-model-api-key" v-model="modelForm.apiKey" :type="showModelApiKey ? 'text' : 'password'" autocomplete="new-password" spellcheck="false" placeholder="粘贴该服务商的 API Key" @input="invalidateConnectionTest" /><button class="api-key-visibility-button" type="button" :aria-label="showModelApiKey ? '隐藏 API Key' : '显示 API Key'" :aria-pressed="showModelApiKey" :title="showModelApiKey ? '隐藏 API Key' : '显示 API Key'" @click="showModelApiKey = !showModelApiKey"><svg v-if="showModelApiKey" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 10.6a2 2 0 0 0 2.8 2.8"/><path d="M9.9 4.3A10.7 10.7 0 0 1 12 4c5 0 8.5 4.2 9.5 6.5a3.5 3.5 0 0 1 0 3A13 13 0 0 1 19 17.1M6.2 6.2a13 13 0 0 0-3.7 4.3 3.5 3.5 0 0 0 0 3C3.5 15.8 7 20 12 20a10.7 10.7 0 0 0 3.1-.5"/></svg><svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 10.5C3.5 8.2 7 4 12 4s8.5 4.2 9.5 6.5a3.5 3.5 0 0 1 0 3C20.5 15.8 17 20 12 20s-8.5-4.2-9.5-6.5a3.5 3.5 0 0 1 0-3Z"/><circle cx="12" cy="12" r="3"/></svg></button></span></div>
              <label v-if="!usesPresetEndpoint" class="full-width">请求地址（API Base URL）<span>填写到 API Base URL 层级，不要附加 /chat/completions。</span><input v-model="modelForm.apiBaseUrl" placeholder="https://your-api-endpoint/v1" @input="invalidateConnectionTest" /></label>
            </div>
            <fieldset class="capability-fieldset"><legend>模型能力</legend><label class="capability-option"><input v-model="modelForm.text" type="checkbox" @change="invalidateConnectionTest" /> <span><strong>文字与 PDF 文本</strong><small>用于职位匹配、简历建议和面试准备</small></span></label><label class="capability-option"><input v-model="modelForm.vision" type="checkbox" @change="invalidateConnectionTest" /> <span><strong>图片简历</strong><small>仅在当前模型确实支持视觉输入时勾选</small></span></label><label class="capability-option"><input v-model="modelForm.tools" type="checkbox" @change="invalidateConnectionTest" /> <span><strong>Tool Calling</strong><small>模型确认支持 function tools 时勾选；未支持时只能执行 Skill 的文字工作流</small></span></label></fieldset>
            <p v-if="connectionTestMessage" class="connection-test-success">{{ connectionTestMessage }}</p>
            <p v-if="connectionTestError" class="connection-test-error" role="alert"><strong>连接测试未通过</strong>{{ connectionTestError }}</p>
            <p v-if="connectionSaveError" class="connection-test-error" role="alert"><strong>保存模型连接失败</strong>{{ connectionSaveError }}</p>
          </section>
        </main>

        <footer v-if="modelDialogMode === 'setup'" class="model-dialog-footer">
          <button class="dialog-secondary-button" type="button" @click="modelDialogMode = modelSetupReturnMode">取消</button>
          <button v-if="modelDialogMode === 'setup'" class="dialog-secondary-button test-connection-button" type="button" :disabled="testingConnection || savingModel" @click="testModelConnection">{{ testingConnection ? '正在测试连接…' : '测试连接' }}</button>
          <button v-if="modelDialogMode === 'setup'" class="dialog-primary-button" type="button" :disabled="savingModel || testingConnection || testedConnectionFingerprint !== connectionFingerprint()" @click="saveModelProfile">{{ savingModel ? '正在保存并复测…' : '保存模型连接' }}</button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.career-workspace { display:grid; height:100%; min-height:0; flex:1; grid-template-columns:300px minmax(0,1fr); gap:14px; overflow:hidden; }
.career-workspace.has-context-rail { grid-template-columns:300px minmax(560px,1fr) var(--context-rail-width,620px); }
.career-workspace.history-collapsed { grid-template-columns:52px minmax(0,1fr); }
.career-workspace.has-context-rail.history-collapsed { grid-template-columns:52px minmax(560px,1fr) var(--context-rail-width,620px); }
.career-history-panel,.career-chat-panel { border:1px solid #e0e6d8; border-radius:20px; background:#fff; box-shadow:none; }
.compact-panel-triggers,.compact-panel-close,.career-panel-scrim{display:none}
.compact-panel-trigger,.compact-panel-close{min-width:40px;min-height:40px;border:1px solid #dbe5d2;border-radius:11px;background:#fff;color:#557044;font-size:12px;font-weight:850}
.context-rail-slot{position:relative;height:100%;min-width:0;min-height:0;overflow:visible}.context-rail-slot>:deep(.context-rail){height:100%;max-height:100%}.context-rail-resizer{position:absolute;z-index:30;top:0;bottom:0;left:-13px;width:12px;border:0;background:transparent;padding:0;cursor:col-resize;touch-action:none}.context-rail-resizer:before{position:absolute;top:0;bottom:0;left:5px;width:2px;border-radius:999px;background:transparent;content:"";transition:background .15s ease,box-shadow .15s ease}.context-rail-resizer i{position:absolute;top:50%;left:1px;display:block;width:10px;height:42px;transform:translateY(-50%);border:1px solid var(--ui-line-strong);border-radius:999px;background:var(--ui-surface);box-shadow:0 3px 12px rgba(31,60,96,.1)}.context-rail-resizer i:after{position:absolute;top:50%;left:3px;width:2px;height:18px;transform:translateY(-50%);border-radius:999px;background:var(--ui-accent);content:""}.context-rail-resizer:hover:before,.context-rail-resizer:focus-visible:before,.resizing-context-rail .context-rail-resizer:before{background:var(--ui-accent);box-shadow:0 0 0 3px var(--ui-focus)}.context-rail-resizer:focus-visible{outline:0}
.career-history-panel { display:flex; height:100%; min-height:0; flex-direction:column; gap:0; overflow:hidden; padding:14px; }
.career-history-panel.collapsed{align-items:center;gap:9px;padding:10px 7px;border-radius:16px}.history-actions{display:grid;grid-template-columns:minmax(0,1fr) 34px;gap:7px;margin-bottom:14px}.history-collapse-button,.history-rail-button{display:grid;place-items:center;border:1px solid var(--ui-line);border-radius:10px;background:var(--ui-surface-soft);color:var(--ui-text-secondary);font-size:22px;font-weight:850;cursor:pointer}.history-collapse-button:hover,.history-rail-button:hover{border-color:var(--ui-accent);background:var(--ui-surface-active);color:var(--ui-accent-ink)}.history-rail-button{width:36px;height:36px}.history-rail-button.primary{border-color:var(--ui-accent);background:var(--ui-accent);color:#fff;font-size:18px}.history-rail-button:disabled{cursor:wait;opacity:.6}.history-rail-count{display:grid;min-width:25px;height:25px;place-items:center;border-radius:999px;background:var(--ui-surface-soft);color:var(--ui-text-muted);font-size:10px;font-weight:850}
.primary-button,.send-button { border:0; border-radius:13px; background:#89a93e; color:#fff; font-weight:800; }
.primary-button { padding:12px 14px; }.primary-button:disabled,.send-button:disabled { cursor:wait; opacity:.6; }
.history-title,.chat-header,.composer-toolbar,.composer-footer,.session-tools,.model-profile-list article { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.history-title { margin-bottom:9px; padding:0 3px; color:var(--ui-text-muted); font-size:12px; font-weight:850; }.history-title small { display:grid; min-width:29px; height:23px; place-items:center; border-radius:999px; background:var(--ui-surface-active); color:var(--ui-accent-ink); }
.conversation-list { display:flex; min-height:0; flex:1; flex-direction:column; gap:6px; overflow-y:auto; overscroll-behavior:contain; scrollbar-width:thin; transition:opacity .15s ease; }.conversation-list.is-loading{opacity:.55;pointer-events:none}.list-empty { display:grid; min-height:0; flex:1; place-content:center; color:var(--ui-text-muted); font-size:13px; text-align:center; }
.conversation-item { position:relative; display:grid; width:100%; height:72px; min-height:72px; flex:0 0 72px; box-sizing:border-box; grid-template-columns:minmax(0,1fr) auto; overflow:visible; border:1px solid transparent; border-radius:10px; background:transparent; color:var(--ui-text-secondary); text-align:left; transition:border-color .14s ease,background .14s ease; }.conversation-item:hover{border-color:var(--ui-line);background:var(--ui-surface-soft)}.conversation-item.active { border-color:var(--ui-line-strong); background:var(--ui-surface-active); color:var(--ui-accent-ink); }.conversation-item.active::before{position:absolute;top:11px;bottom:11px;left:-1px;width:3px;border-radius:0 3px 3px 0;background:var(--ui-accent);content:""}.conversation-item.managing{height:auto;min-height:72px;flex:0 0 auto;overflow:hidden}
.conversation-item-main { display:grid; min-width:0; align-content:center; gap:6px; border:0; border-radius:9px; background:transparent; color:inherit; padding:10px 3px 9px 14px; text-align:left; cursor:pointer; }.conversation-item-main:focus-visible{outline:3px solid var(--ui-focus);outline-offset:-3px}.conversation-item-main:disabled { cursor:wait; opacity:.62; }.conversation-item-main strong { overflow:hidden; min-width:0; font-size:12px; line-height:1.4; text-overflow:ellipsis; white-space:nowrap; }.conversation-item-main small { color:var(--ui-text-muted); font-size:10px; white-space:nowrap; }
.conversation-menu-button { align-self:center; border:0; border-radius:7px; background:transparent; color:var(--ui-text-muted); padding:5px 7px; font-size:15px; font-weight:900; line-height:1; opacity:0; cursor:pointer; transition:opacity .14s ease,background .14s ease,color .14s ease; }.conversation-item:hover .conversation-menu-button,.conversation-item.active .conversation-menu-button,.conversation-menu-button:focus-visible,.conversation-menu-button[aria-expanded="true"]{opacity:1}.conversation-menu-button:hover,.conversation-menu-button[aria-expanded="true"] { background:var(--ui-surface); color:var(--ui-accent-ink); }.conversation-menu-button:focus-visible{outline:3px solid var(--ui-focus);outline-offset:1px}.conversation-menu-button:disabled { cursor:not-allowed; opacity:.45; }
.conversation-action-menu { display:grid; grid-column:1 / -1; gap:9px; border-top:1px solid var(--ui-line); background:var(--ui-surface); padding:10px; }.conversation-action-menu label { display:grid; gap:5px; color:var(--ui-text-secondary); font-size:11px; font-weight:800; }.conversation-action-menu input { width:100%; box-sizing:border-box; border:1px solid var(--ui-line); border-radius:8px; background:var(--ui-surface); color:var(--ui-text); padding:8px 9px; font:inherit; }.conversation-action-menu input:focus { border-color:var(--ui-accent); box-shadow:0 0 0 3px var(--ui-focus); outline:0; }.conversation-action-menu p { display:grid; gap:3px; margin:0; }.conversation-action-menu p strong { color:var(--ui-text); font-size:12px; }.conversation-action-menu p span { color:var(--ui-text-muted); font-size:11px; line-height:1.5; }
.conversation-action-options,.conversation-action-buttons { display:flex; gap:7px; }.conversation-action-buttons { justify-content:flex-end; }.conversation-action-menu button { border:1px solid var(--ui-line); border-radius:7px; background:var(--ui-surface); color:var(--ui-text-secondary); padding:7px 9px; font-size:11px; font-weight:800; cursor:pointer; }.conversation-action-options button { flex:1; }.conversation-action-menu button:hover { background:var(--ui-surface-soft); }.conversation-action-menu button.primary { border-color:var(--ui-accent); background:var(--ui-accent); color:#fff; }.conversation-action-menu button.danger { border-color:#efcaca; background:#fff6f6; color:#b54747; }.conversation-action-menu button:disabled { cursor:wait; opacity:.6; }
.history-pagination-shell{min-height:76px;flex:0 0 auto;margin-top:10px;border-top:1px solid var(--ui-line);border-bottom:1px solid var(--ui-line);padding:8px 4px}.history-pagination-shell :deep(.el-pagination){display:grid;width:100%;box-sizing:border-box;grid-template-columns:28px minmax(0,1fr) 28px;grid-template-rows:28px 28px;align-items:center;gap:6px 4px;--el-color-primary:var(--ui-accent);--el-pagination-button-width:28px;--el-pagination-button-height:28px;--el-pagination-button-bg-color:var(--ui-surface-soft);--el-pagination-hover-color:var(--ui-accent-ink);font-family:inherit}.history-pagination-shell :deep(.el-pagination__total){grid-column:1 / 3;grid-row:1;justify-self:start;margin:0;color:var(--ui-text-muted);font-size:11px;font-weight:750}.history-pagination-shell :deep(.el-pagination__sizes){grid-column:2 / 4;grid-row:1;justify-self:end;margin:0}.history-pagination-shell :deep(.el-pagination__sizes .el-select){width:94px}.history-pagination-shell :deep(.el-pagination__sizes .el-select__wrapper){min-height:28px;border-radius:6px;box-shadow:0 0 0 1px var(--ui-line) inset}.history-pagination-shell :deep(.btn-prev){grid-column:1;grid-row:2}.history-pagination-shell :deep(.el-pager){grid-column:2;grid-row:2;min-width:0;justify-self:center}.history-pagination-shell :deep(.btn-next){grid-column:3;grid-row:2}.history-pagination-shell :deep(.btn-prev),.history-pagination-shell :deep(.btn-next),.history-pagination-shell :deep(.el-pager li){border-radius:6px;font-weight:750}.history-pagination-shell :deep(.el-pager li.is-active){box-shadow:0 3px 8px rgba(8,103,217,.2)}.history-pagination-shell :deep(button:focus-visible),.history-pagination-shell :deep(.el-select__wrapper.is-focused){outline:3px solid var(--ui-focus);outline-offset:1px}.privacy-note{display:flex;min-height:44px;flex:0 0 auto;align-items:center;gap:7px;padding:8px 7px 0;color:var(--ui-text-muted)}.privacy-note>span{display:grid;width:20px;height:20px;flex:0 0 auto;place-items:center;border-radius:6px;background:var(--ui-surface-active);color:var(--ui-accent-ink);font-size:9px;font-weight:850}.privacy-note p{margin:0;font-size:9px;line-height:1.45}.eyebrow { color:var(--ui-accent-ink); font-size:9px; font-weight:900; letter-spacing:.08em; }
.career-chat-panel { display:flex; height:100%; min-height:0; flex-direction:column; overflow:hidden; }.chat-header { display:flex; min-height:68px; align-items:center; border-bottom:1px solid #e3ebf5; padding:10px 14px 10px 20px; background:#fbfdff; }.chat-header>div:first-child{min-width:0}.chat-header h2,.model-settings h3,.empty-state h2 { margin:3px 0 0; color:#10294c; }.active-context-line{display:flex;align-items:center;gap:7px;margin:5px 0 0;color:#6d7f98;font-size:11px;font-weight:750}.active-context-line span{max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.active-context-line i{color:#0a67db;font-style:normal}.eyebrow { margin:0; }
.chat-material-actions{display:flex;flex:none;align-items:center;gap:7px}.chat-material-button{position:relative;display:inline-flex;min-height:38px;align-items:center;justify-content:center;gap:7px;border:1px solid var(--ui-line-strong,#bfd2ea);border-radius:9px;background:var(--ui-surface,#fff);color:var(--ui-text-secondary,#526078);padding:8px 12px;font:800 12px/1 var(--ui-font-body,"Segoe UI",sans-serif);white-space:nowrap;cursor:pointer;transition:border-color .15s ease,background .15s ease,color .15s ease,box-shadow .15s ease}.chat-material-button svg{width:16px;height:16px;flex:none;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}.chat-material-button:hover:not(:disabled),.chat-material-button:focus-visible{border-color:var(--ui-accent,#0869d8);background:var(--ui-surface-active,#eaf3ff);color:var(--ui-accent-ink,#004aa8);box-shadow:0 0 0 3px var(--ui-focus,rgba(8,105,216,.14));outline:0}.chat-material-button.primary{border-color:var(--ui-accent,#0869d8);background:var(--ui-accent,#0869d8);color:#fff;padding-right:14px;padding-left:14px}.chat-material-button.primary:hover:not(:disabled),.chat-material-button.primary:focus-visible{background:var(--ui-accent-strong,#0056bd);color:#fff}.chat-material-button:disabled{cursor:wait;opacity:.58}.material-state-dot{width:6px;height:6px;flex:none;border-radius:50%;background:var(--ui-success,#21855b);box-shadow:0 0 0 2px var(--ui-success-soft,#e8f7f0)}
.chat-material-button.interview-master{border-color:#9fc4f0;background:#edf6ff;color:#075dbd}.chat-material-button.interview-master:hover:not(:disabled),.chat-material-button.interview-master:focus-visible{border-color:#0869d8;background:#deedff;color:#004da8}
.chat-material-button.online-assessment{border-color:#a8c5ea;background:#f1f6fd;color:#165aab}.chat-material-button.online-assessment:hover:not(:disabled),.chat-material-button.online-assessment:focus-visible{border-color:#176fe5;background:#e6f1ff;color:#0d4fa8}
.chat-material-button.greeting{border-color:var(--ui-accent,#0869d8);background:var(--ui-surface-active,#eaf3ff);color:var(--ui-accent-ink,#004aa8)}
.quiet-button,.chip-button { border:1px solid #dfe8d0; border-radius:10px; background:#f8fbf1; color:#61764b; padding:8px 11px; font-size:12px; font-weight:800; }.quiet-button.danger { border-color:#f0d5d5; background:#fff8f8; color:#ad5a5a; }
.notice { margin:14px 22px 0; border-radius:12px; padding:10px 13px; font-size:13px; }.notice.success { border:1px solid #d7eabe; background:#f5faec; color:#5c7a2c; }
.model-settings { display:grid; grid-template-columns:minmax(220px,.75fr) minmax(360px,1.25fr); gap:18px; border-bottom:1px solid #edf0e7; background:#fbfcf8; padding:18px 22px; }.model-settings p:not(.eyebrow) { color:#7b8574; font-size:13px; line-height:1.6; }
.model-form { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }.model-form label { display:grid; gap:5px; color:#6e7b63; font-size:12px; font-weight:700; }.model-form input,.model-form select,.job-url-row input,.composer textarea,.model-select { width:100%; box-sizing:border-box; border:1px solid #dfe7d4; border-radius:10px; background:#fff; color:#324032; font:inherit; outline:none; }.model-form input,.model-form select { padding:8px 9px; }.check-label { display:flex !important; align-items:center; gap:7px; }.check-label input { width:auto; }.save-model { grid-column:1 / -1; padding:10px; }
.model-profile-list { grid-column:1 / -1; display:grid; gap:7px; }.model-profile-list article { border:1px solid #e8ede0; border-radius:11px; background:#fff; padding:9px 11px; }.model-profile-list strong,.model-profile-list small { display:block; }.model-profile-list strong { color:#3a4837; font-size:13px; }.model-profile-list small { margin-top:3px; color:#8a9582; font-size:11px; }.readiness { border-radius:999px; padding:5px 8px; font-size:11px; font-weight:800; white-space:nowrap; }.readiness.ready { background:#edf7df; color:#5c7d27; }.readiness.credential_required { background:#fff4d9; color:#a16d12; }.readiness.policy_blocked { background:#fff0f0; color:#a65a5a; }.readiness.disabled { background:#f0f2ee; color:#7d8779; }
.empty-state { display:grid; flex:1; place-content:center; justify-items:center; padding:50px 28px; text-align:center; }.empty-mark { display:grid; width:44px; height:44px; place-items:center; border-radius:14px; background:#89a93e; color:#fff; font-size:21px; }.empty-state > p:not(.eyebrow) { max-width:530px; color:#78836f; line-height:1.7; }.tag-row { display:flex; flex-wrap:wrap; justify-content:center; gap:8px; }.tag-row span { border-radius:999px; background:#f1f7e5; color:#668329; padding:7px 10px; font-size:12px; font-weight:800; }
.empty-context-button{border:0;border-radius:11px;background:#0867d9;color:#fff;padding:11px 16px;font-weight:850;cursor:pointer}.active-context-chip{max-width:300px!important;border-color:#b8d1ed!important;background:#edf6ff!important;color:#0d5eB9!important}
.message-list { display:flex; flex:1; flex-direction:column; gap:16px; overflow-y:auto; overscroll-behavior:contain; padding:22px 24px; }
.message,.agent-message { box-sizing:border-box; max-width:min(820px,86%); padding:14px 16px; }
.agent-message,.message.from-agent { align-self:flex-start; border:1px solid var(--ui-line); border-left:3px solid var(--ui-accent); border-radius:5px 13px 13px 5px; background:var(--ui-surface); box-shadow:0 8px 24px rgba(27,51,92,.045); }
.message.from-user { width:fit-content; max-width:min(620px,72%); align-self:flex-end; border:1px solid #cfe0f5; border-radius:14px 14px 4px 14px; background:#edf5ff; padding:12px 15px 9px; box-shadow:none; }
.message-role { display:flex; align-items:center; gap:7px; margin-bottom:10px; color:var(--ui-text-secondary); }
.message-role i { display:grid; width:24px; height:24px; flex:0 0 auto; place-items:center; border-radius:7px; background:var(--ui-surface-active); color:var(--ui-accent-ink); font-size:11px; font-style:normal; font-weight:900; }
.message-role strong { color:var(--ui-text-secondary); font-size:12px; font-weight:800; }
.message-model-label { overflow:hidden; max-width:360px; border:1px solid var(--ui-line); border-radius:999px; background:var(--ui-surface-soft); padding:3px 7px; color:var(--ui-text-muted); font-size:10px; font-weight:750; text-overflow:ellipsis; white-space:nowrap; }
.message-time { display:block; margin-top:10px; color:var(--ui-text-muted); font-size:10px; }.from-user .message-time { margin-top:6px; color:#8193aa; text-align:right; }
.turn-status { align-self:center; border-radius:999px; background:var(--ui-surface-soft); color:var(--ui-text-secondary); padding:7px 11px; font-size:12px; }.turn-status.active { background:var(--ui-warning-soft); color:var(--ui-warning); }
.message-sending,.message-queued { opacity:.76; }.message-failed { border-color:#edcaca; background:#fff8f8 !important; }.message-failed small { color:#b35454; font-weight:800; }.agent-pending { width:min(560px,86%); color:var(--ui-text-secondary); }.stream-pending-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; color:var(--ui-text-secondary); }.stream-pending-heading .message-role { margin-bottom:0; }.stream-pending-heading::before { width:13px; height:13px; flex:0 0 auto; border:2px solid var(--ui-line-strong); border-top-color:var(--ui-accent); border-radius:50%; content:''; animation:career-thinking-spin .8s linear infinite; }.stream-pending-heading span { color:var(--ui-text-muted); font-size:11px; font-weight:800; }.stream-progress-list { display:grid; gap:7px; margin:11px 0 0; padding:0; list-style:none; }.stream-progress-item { display:flex; align-items:center; gap:8px; color:var(--ui-text-muted); font-size:12px; line-height:1.45; }.stream-progress-item i { width:7px; height:7px; flex:0 0 auto; border-radius:50%; background:var(--ui-line-strong); }.stream-progress-item.running { color:var(--ui-accent-ink); font-weight:750; }.stream-progress-item.running i { background:var(--ui-accent); animation:career-progress-pulse 1s ease-in-out infinite; }.stream-progress-item.completed i { background:var(--ui-success); }.stream-status,.streamed-answer { margin:10px 0 0 !important; }.streamed-answer { border-top:1px solid var(--ui-line); padding-top:10px; } @keyframes career-progress-pulse { 50% { transform:scale(.72); opacity:.55; } }
.message-scroll-anchor { display:block; width:100%; height:1px; pointer-events:none; }
.turn-queue-preview{display:grid;width:min(560px,86%);gap:7px;border:1px dashed var(--ui-line-strong);border-radius:12px;background:var(--ui-surface-soft);padding:10px 12px;color:var(--ui-text-secondary)}.turn-queue-preview>strong{font-size:11px}.turn-queue-preview article{display:grid;grid-template-columns:20px minmax(0,1fr) auto;align-items:start;gap:7px}.turn-queue-preview article span{display:grid;width:18px;height:18px;place-items:center;border-radius:50%;background:var(--ui-surface-active);color:var(--ui-accent-ink);font-size:10px;font-weight:850}.turn-queue-preview article p{overflow:hidden;margin:0;font-size:12px;line-height:1.45;text-overflow:ellipsis;white-space:nowrap}.turn-queue-preview article small{border-radius:999px;background:#fff;padding:2px 6px;color:var(--ui-text-muted);font-size:10px;font-weight:800}
@keyframes career-thinking-spin { to { transform:rotate(360deg); } }
.composer { border-top:1px solid #edf0e7; background:#fff; padding:12px 16px 14px; }.composer-toolbar { min-height:36px; flex-wrap:wrap; border-bottom:1px solid #edf0e7; padding-bottom:9px; }.input-tools,.session-tools { display:flex; min-width:0; flex-wrap:wrap; align-items:center; gap:7px; }.session-tools { margin-left:auto; justify-content:flex-end; }.job-url-row { display:grid; grid-template-columns:auto 1fr; align-items:center; gap:8px; margin:10px 0 9px; color:#738068; font-size:12px; font-weight:800; }.job-url-row input,.composer textarea { padding:10px 11px; }.composer textarea { display:block; min-height:68px; resize:vertical; }.composer-footer { margin-top:9px; }.composer-submit-tools { display:flex; flex:none; align-items:center; gap:10px; }.input-tools { justify-content:flex-start; }.resume-input { display:none; }.chip-button.active { max-width:240px; overflow:hidden; background:#eaf4d4; color:#5a7d21; text-overflow:ellipsis; white-space:nowrap; }.file-clear-button { border-color:#f0d5d5; background:#fff8f8; color:#a65a5a; }.free-model-entry-button { border-style:dashed; background:#fff; color:#62812d; }.free-model-entry-button:hover { border-color:#9fbd67; background:#f5f9ed; }.model-select { width:auto; max-width:300px; padding:7px 9px; color:#65775a; font-size:12px; font-weight:700; }.send-button { min-width:88px; padding:9px 13px; }.composer-footer > small { color:#98a18e; font-size:11px; }
.turn-limit-notice { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-top:9px; border-radius:10px; background:#f7f4e6; padding:8px 10px; color:#75672d; font-size:12px; }.turn-limit-notice.blocked { background:#fff0ee; color:#9a4b40; }
.composer-toolbar{min-height:32px;flex-wrap:nowrap;gap:6px;border:1px solid var(--ui-line);border-radius:10px;background:var(--ui-surface-soft);padding:5px 6px}.composer-toolbar .input-tools,.composer-toolbar .session-tools{flex-wrap:nowrap;gap:5px}.composer-toolbar .input-tools{flex:1}.composer-toolbar :is(.chip-button,.quiet-button,.model-select){height:30px;border-color:var(--ui-line);border-radius:8px;background:var(--ui-surface);padding:5px 9px;color:var(--ui-text-secondary);font-size:11px}.composer-toolbar .active-context-chip{max-width:132px!important;flex:none;border-color:var(--ui-line-strong)!important;background:var(--ui-surface-active)!important;color:var(--ui-accent-ink)!important}.composer-toolbar .model-select{min-width:132px;max-width:230px;flex:1 1 164px}.composer-toolbar .free-model-entry-button{flex:none;border-style:solid;background:var(--ui-surface);color:var(--ui-accent-ink)}.composer-toolbar .model-manager-button{flex:none}.conversation-action-options button{min-width:0;padding-right:6px;padding-left:6px}
.composer-context-meter{flex:none}
@media(max-width:640px){.composer-toolbar{flex-wrap:wrap}.composer-toolbar .input-tools,.composer-toolbar .session-tools{width:100%;flex-wrap:wrap}.composer-toolbar .model-select{max-width:100%;flex:1 1 150px}}
.composer-input-shell { --composer-skill-ink: #0869d8; --composer-skill-wash: #eaf3ff; --composer-interview-ink: #b45309; --composer-interview-wash: #fff1dc; position:relative; margin-top:10px; }
.composer-highlight-layer,.composer-input-shell textarea { box-sizing:border-box; width:100%; min-height:68px; padding:10px 11px; font:inherit; font-size:13px; line-height:1.6; letter-spacing:normal; overflow-wrap:break-word; tab-size:4; white-space:pre-wrap; word-break:break-word; }
.composer-highlight-layer { position:absolute; z-index:3; inset:0; overflow:auto; border:1px solid transparent; border-radius:10px; background:transparent; color:transparent; pointer-events:none; scrollbar-width:none; }
.composer-highlight-layer::-webkit-scrollbar { display:none; }
.composer-input-shell textarea { position:relative; z-index:2; margin:0; background:#fff; caret-color:#17263a; color:#324032; -webkit-text-fill-color:currentColor; }
.composer-input-shell textarea::placeholder { color:#98a4b3; -webkit-text-fill-color:#98a4b3; }
.composer-input-shell textarea::selection { background:rgba(8,105,216,.18); }
.composer-highlight-layer mark { border-radius:4px; background:transparent; box-decoration-break:clone; font:inherit; -webkit-box-decoration-break:clone; }
.composer-highlight-layer .composer-highlight-skill { background:var(--composer-skill-wash); box-shadow:0 0 0 1px rgba(8,105,216,.12); color:var(--composer-skill-ink); }
.composer-highlight-layer .composer-highlight-interview { background:var(--composer-interview-wash); box-shadow:0 0 0 1px rgba(180,83,9,.13); color:var(--composer-interview-ink); }
.interview-mention-menu { display:grid; max-height:206px; margin-top:8px; overflow:auto; border:1px solid #dce6ce; border-radius:12px; background:#fff; box-shadow:0 12px 28px rgba(68,84,44,.12); }
.interview-mention-menu button { display:grid; gap:3px; border:0; border-bottom:1px solid #edf2e7; background:#fff; color:#34472a; padding:10px 12px; text-align:left; cursor:pointer; }
.interview-mention-menu button:last-child { border-bottom:0; }.interview-mention-menu button:hover { background:#f3f8e9; }.interview-mention-menu strong { font-size:13px; }.interview-mention-menu small { color:#87977b; font-size:12px; }
.skill-invocation-menu { border-color:#bcd8f6; }.suggestion-menu-label { margin:0; border-bottom:1px solid #dceafb; background:#f2f7fd; color:#2869b5; padding:7px 12px; font-size:10px; font-weight:900; letter-spacing:.08em; text-transform:uppercase; }.skill-invocation-menu button:hover { background:#edf6ff; }
.career-error-toast { position:fixed; z-index:1400; top:50%; left:50%; display:flex; width:min(620px,calc(100vw - 40px)); box-sizing:border-box; align-items:flex-start; justify-content:space-between; gap:18px; transform:translate(-50%,-50%); border:1px solid #efbcbc; border-radius:18px; background:#fff8f8; box-shadow:0 22px 70px rgba(114,42,42,.24); color:#943f3f; padding:19px 18px 19px 21px; animation:career-toast-in .18s ease-out; }.career-error-toast strong { display:block; font-size:17px; line-height:1.35; }.career-error-toast p { margin:5px 0 0; font-size:16px; font-weight:650; line-height:1.6; }.toast-close-button { display:grid; width:30px; height:30px; flex:0 0 auto; place-items:center; border:1px solid #edcaca; border-radius:9px; background:#fff; color:#a95050; font-size:22px; line-height:1; }.toast-close-button:hover { background:#fff0f0; }@keyframes career-toast-in { from { opacity:0; transform:translate(-50%,-46%); } to { opacity:1; transform:translate(-50%,-50%); } }
.model-manager-button { border-color:#cfdcb7; background:#f1f7e5; color:#5c7a28; }.model-dialog-backdrop { position:fixed; z-index:1200; inset:0; display:grid; place-items:center; box-sizing:border-box; padding:28px; background:rgba(29,40,25,.42); backdrop-filter:blur(5px); }.model-dialog { display:flex; width:min(960px,100%); max-height:min(780px,calc(100vh - 56px)); flex-direction:column; overflow:hidden; border:1px solid #dce6cf; border-radius:24px; background:#fff; box-shadow:0 28px 80px rgba(23,37,20,.28); }.model-dialog-header { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; border-bottom:1px solid #ecf0e5; padding:24px 28px 20px; }.model-dialog-header h2 { margin:4px 0 0; color:#243323; font-size:23px; }.dialog-close-button { display:grid; width:34px; height:34px; flex:0 0 auto; place-items:center; border:1px solid #e1e8d7; border-radius:10px; background:#fafcf7; color:#728067; font-size:23px; line-height:1; }.model-dialog-body { min-height:240px; overflow-y:auto; padding:22px 28px 26px; }.connection-toolbar { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:16px; }.connection-toolbar strong,.connection-toolbar small { display:block; }.connection-toolbar strong { color:#354333; font-size:16px; }.connection-toolbar small,.dialog-helper { margin-top:4px; color:#889281; font-size:12px; }.dialog-primary-button,.dialog-secondary-button { border:0; border-radius:11px; padding:10px 15px; font-size:13px; font-weight:850; }.dialog-primary-button { background:#89a93e; color:#fff; box-shadow:0 8px 18px rgba(112,144,51,.2); }.dialog-primary-button:disabled { cursor:wait; opacity:.6; }.dialog-secondary-button,.back-button { border:1px solid #e1e8d7; background:#fff; color:#64735a; }.connection-card-list { display:grid; gap:10px; }.connection-card { display:grid; width:100%; grid-template-columns:18px 42px minmax(0,1fr) auto; align-items:center; gap:13px; border:1px solid #e3ead9; border-radius:15px; background:#fff; color:#31402f; padding:13px 15px; text-align:left; transition:border-color .16s ease,background .16s ease,transform .16s ease; }.connection-card:hover { border-color:#a7c66d; background:#fbfdf7; transform:translateY(-1px); }.connection-drag { color:#bcc7b4; font-size:20px; letter-spacing:-3px; }.provider-avatar { display:grid; width:38px; height:38px; place-items:center; border:1px solid #dfe8d0; border-radius:12px; background:#f3f8e9; color:#6d8c33; font-size:11px; font-weight:900; }.provider-avatar.large { width:44px; height:44px; border-radius:14px; }.connection-card-copy { min-width:0; }.connection-card-copy strong,.connection-card-copy small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.connection-card-copy strong { font-size:14px; }.connection-card-copy small { margin-top:3px; color:#899481; font-size:12px; }.connection-meta { display:grid; justify-items:end; gap:5px; }.connection-meta small { color:#98a18f; font-size:11px; }.connection-empty-state { display:grid; min-height:250px; place-content:center; justify-items:center; border:1px dashed #d9e4cb; border-radius:16px; background:#fbfdf8; color:#7a8870; text-align:center; }.connection-empty-state > span { color:#90b04c; font-size:30px; }.connection-empty-state strong { margin-top:8px; color:#526449; }.connection-empty-state p { max-width:320px; margin:7px 0 0; font-size:13px; line-height:1.6; }.back-button { border-radius:9px; padding:7px 10px; color:#66765b; font-size:12px; font-weight:800; }.model-dialog-body h3 { margin:20px 0 4px; color:#31402e; font-size:18px; }.provider-picker-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:18px; }.provider-picker-card { display:grid; grid-template-columns:44px minmax(0,1fr); align-items:center; gap:11px; border:1px solid #e2ead8; border-radius:16px; background:#fbfcf9; color:#334131; padding:15px; text-align:left; transition:border-color .16s ease,box-shadow .16s ease; }.provider-picker-card:hover { border-color:#97ba57; box-shadow:0 10px 25px rgba(91,120,42,.1); }.provider-picker-card strong,.provider-picker-card small { display:block; }.provider-picker-card small { margin-top:4px; color:#84907c; font-size:11px; line-height:1.5; }.provider-picker-card em { grid-column:1 / -1; justify-self:start; border-radius:999px; background:#eef6df; color:#66842e; padding:4px 7px; font-size:10px; font-style:normal; font-weight:850; }.connection-form-body { padding-bottom:22px; }.selected-provider-banner { display:flex; align-items:center; gap:11px; margin-top:17px; border:1px solid #dce9c8; border-radius:15px; background:#f6faef; padding:12px; }.selected-provider-banner div { min-width:0; flex:1; }.selected-provider-banner strong,.selected-provider-banner small { display:block; }.selected-provider-banner small { margin-top:3px; color:#7d8973; font-size:12px; }.selected-provider-banner > span:last-child { border-radius:999px; background:#e7f1d4; color:#63822b; padding:5px 8px; font-size:11px; font-weight:850; }.connection-form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin-top:18px; }.connection-form-grid label { display:grid; gap:5px; color:#4e5c49; font-size:13px; font-weight:850; }.connection-form-grid label > span { color:#8a9583; font-size:11px; font-weight:500; }.connection-form-grid input,.connection-form-grid select { width:100%; box-sizing:border-box; border:1px solid #dfe7d4; border-radius:10px; background:#fff; color:#354334; padding:10px 11px; font:inherit; outline:none; }.connection-form-grid input:focus,.connection-form-grid select:focus { border-color:#91b44b; box-shadow:0 0 0 3px rgba(137,169,62,.12); }.full-width { grid-column:1 / -1; }.capability-fieldset { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin:18px 0 0; border:0; padding:0; }.capability-fieldset legend { margin-bottom:8px; color:#4d5d48; font-size:13px; font-weight:850; }.capability-option { display:flex; align-items:flex-start; gap:8px; border:1px solid #e3ead9; border-radius:13px; background:#fbfcf9; padding:11px; }.capability-option input { margin-top:3px; accent-color:#89a93e; }.capability-option strong,.capability-option small { display:block; }.capability-option strong { color:#485846; font-size:12px; }.capability-option small { margin-top:3px; color:#899481; font-size:11px; line-height:1.45; }.connection-test-success,.connection-test-error { display:block; margin:14px 0 0; border-radius:11px; padding:10px 12px; font-size:12px; font-weight:750; line-height:1.6; }.connection-test-success { border:1px solid #cce5aa; background:#f4faea; color:#587b27; }.connection-test-error { border:1px solid #efcaca; background:#fff6f6; color:#9c4848; }.connection-test-error strong { display:block; margin-bottom:2px; }.model-dialog-footer { display:flex; justify-content:flex-end; gap:10px; border-top:1px solid #ecf0e5; background:#fbfcf9; padding:15px 28px; }
.model-dialog { width:min(1080px,100%); max-height:min(840px,calc(100vh - 44px)); }
.connection-setup-body { scroll-behavior:smooth; }
.api-key-field { display:grid; gap:5px; }
.api-key-input-shell { position:relative; display:block; color:inherit; font-size:13px; font-weight:850; }
.connection-form-grid .api-key-input-shell input { padding-right:46px; }
.api-key-visibility-button { position:absolute; top:50%; right:5px; display:grid; width:34px; height:34px; place-items:center; border:0; border-radius:8px; background:transparent; color:#75836f; padding:0; transform:translateY(-50%); cursor:pointer; }
.api-key-visibility-button svg { width:19px; height:19px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
.api-key-visibility-button:hover { background:#f0f6e7; color:#5f7f28; }
.api-key-visibility-button:focus-visible { outline:3px solid rgba(137,169,62,.2); color:#5f7f28; }
.connection-toolbar-actions { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:8px; }
.provider-catalog-section { padding-bottom:22px; border-bottom:1px solid #edf0e7; }
.provider-picker-grid-expanded { grid-template-columns:repeat(4,minmax(0,1fr)); }
.provider-picker-card.selected { border-color:#89a93e; background:#f2f8e7; box-shadow:0 0 0 3px rgba(137,169,62,.12); }
.connection-config-section { scroll-margin-top:14px; padding-top:20px; }
.connection-section-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.connection-section-heading h3 { margin-top:0; }
.test-connection-button { color:#50761b; }
.test-connection-button:disabled { cursor:wait; opacity:.6; }
.free-model-directory { background:linear-gradient(180deg,#fbfdf8 0,#fff 160px); }
.free-directory-intro { display:flex; align-items:flex-start; justify-content:space-between; gap:24px; margin:18px 0 20px; border-bottom:1px solid #e7ecdf; padding-bottom:18px; }.free-directory-intro > div { max-width:690px; }.free-directory-intro span { color:#8aa34c; font-size:10px; font-weight:900; letter-spacing:.14em; }.free-directory-intro h3 { margin:5px 0 4px; font-size:20px; }.free-directory-intro p { margin:0; color:#778372; font-size:12px; line-height:1.65; }.free-directory-intro > strong { flex:0 0 auto; border-radius:999px; background:#eaf3d8; color:#66852d; padding:7px 10px; font-size:11px; }
.free-provider-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }.free-provider-card { display:flex; min-width:0; flex-direction:column; border:1px solid #dfe8d4; border-radius:18px; background:#fff; padding:16px; box-shadow:0 8px 24px rgba(65,82,50,.05); }.free-provider-card > header { display:grid; grid-template-columns:44px minmax(0,1fr) auto; align-items:center; gap:11px; }.free-provider-card > header strong,.free-provider-card > header small { display:block; }.free-provider-card > header strong { color:#31402e; font-size:14px; }.free-provider-card > header small { margin-top:3px; color:#839078; font-size:11px; }.catalog-readiness { border-radius:999px; background:#f4eee1; color:#9a7327; padding:5px 7px; font-size:10px; font-weight:850; }.catalog-readiness.ready { background:#eaf4d8; color:#5f8228; }.free-provider-card > p { min-height:44px; margin:13px 0 9px; color:#727f6c; font-size:12px; line-height:1.65; }.catalog-access-summary { margin-bottom:12px; border-left:3px solid #d9b86c; border-radius:0 9px 9px 0; background:#fff9eb; color:#866922; padding:8px 10px; font-size:11px; font-weight:800; line-height:1.5; }.catalog-access-summary.ready { border-left-color:#8dac4b; background:#f0f7e5; color:#5d7d28; }.free-model-template-list { display:grid; gap:8px; }.free-model-template { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:4px 10px; border:1px solid #e7ecdf; border-radius:12px; background:#fafcf7; padding:10px; }.free-model-template > div { min-width:0; }.free-model-template strong,.free-model-template code { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.free-model-template strong { color:#445340; font-size:12px; }.free-model-template code { margin-top:3px; color:#84917d; font-size:10px; }.free-model-template-meta { display:flex; align-items:center; gap:10px; }.free-model-template-meta small { color:#96a08f; font-size:10px; }.free-model-template-meta a { color:#557825; font-size:10px; font-weight:850; text-decoration:none; white-space:nowrap; }.free-model-template-meta a:hover { text-decoration:underline; }.free-model-template-meta a:focus-visible { border-radius:4px; outline:2px solid currentColor; outline-offset:2px; }.free-model-template button { grid-row:1 / 3; grid-column:2; align-self:center; border:1px solid #b9cf8e; border-radius:9px; background:#f0f7e3; color:#5d7d28; padding:7px 9px; font-size:11px; font-weight:850; }.free-provider-card > footer { display:flex; flex-wrap:wrap; gap:8px 14px; margin-top:auto; padding-top:14px; }.free-provider-card > footer a { color:#557825; font-size:11px; font-weight:850; text-decoration:none; }.free-provider-card > footer a:hover { text-decoration:underline; }
.catalog-load-error { display:flex; align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap; }.catalog-load-error > div { min-width:0; flex:1 1 420px; }.catalog-load-error span { display:block; }.catalog-load-error button { flex:0 0 auto; background:#fff; }
/* 业务区域跟随主内容容器，而不是跟随物理屏幕或浏览器缩放值。 */
@media (max-width:640px) { .career-workspace,.career-workspace.history-collapsed,.career-workspace.has-context-rail.history-collapsed { height:auto; min-height:calc(100dvh - 92px); grid-template-columns:1fr; grid-template-rows:auto auto auto; align-content:start; gap:10px; overflow:visible; }.career-chat-panel { height:auto; min-height:520px; overflow:visible; }.chat-header { min-height:60px; align-items:flex-start; flex-direction:column; padding:12px 14px; }.chat-material-actions{width:100%}.chat-material-button{min-width:0;flex:1}.message-list { min-height:270px; flex:none; overflow:visible; padding:14px; }.composer { position:sticky; z-index:5; bottom:0; padding:10px 12px max(12px, env(safe-area-inset-bottom)); box-shadow:0 -10px 22px rgba(47,62,37,.08); }.composer-toolbar,.composer-footer { align-items:flex-start; flex-direction:column; }.session-tools { margin-left:0; }.career-history-panel { height:auto; max-height:none; overflow:visible; }.career-history-panel.collapsed{height:auto;flex-direction:row;justify-content:flex-start;padding:8px}.conversation-list { grid-template-columns:1fr; max-height:200px; flex:none; overflow:auto; }.model-settings,.model-form,.provider-picker-grid,.provider-picker-grid-expanded { grid-template-columns:1fr; }.message,.agent-message { max-width:94%; }.model-select { max-width:100%; }.send-button { align-self:stretch; min-height:44px; }.career-error-toast { width:calc(100vw - 28px); gap:12px; padding:17px; }.career-error-toast strong { font-size:16px; }.career-error-toast p { font-size:15px; }.model-dialog-backdrop { align-items:end; padding:0; }.model-dialog { max-height:90dvh; border-radius:22px 22px 0 0; }.model-dialog-header,.model-dialog-body,.model-dialog-footer { padding-right:18px; padding-left:18px; }.connection-toolbar,.connection-card { align-items:flex-start; }.connection-toolbar { flex-direction:column; }.connection-toolbar-actions { width:100%; justify-content:stretch; }.connection-toolbar-actions button { flex:1; }.connection-card { grid-template-columns:18px 38px minmax(0,1fr); }.connection-meta { grid-column:3; justify-items:start; }.connection-form-grid,.capability-fieldset { grid-template-columns:1fr; }.dialog-primary-button,.dialog-secondary-button { min-height:44px; }.free-directory-intro { align-items:flex-start; flex-direction:column; gap:10px; }.free-provider-grid { grid-template-columns:1fr; }.free-provider-card { padding:14px; }.free-provider-card > header { grid-template-columns:40px minmax(0,1fr); }.free-provider-card > header .catalog-readiness { grid-column:2; justify-self:start; }.free-provider-card > p { min-height:0; }.free-model-template { grid-template-columns:1fr; }.free-model-template button { grid-row:auto; grid-column:auto; min-height:42px; }.free-provider-card > footer a { min-height:36px; display:inline-flex; align-items:center; } }

@media (max-width:640px) {
  .career-workspace.has-context-rail,
  .career-workspace.has-context-rail.history-collapsed {
    grid-template-columns:1fr;
  }

  .context-rail-resizer {
    display:none;
  }

  .career-history-panel {
    padding:12px;
  }

  .career-chat-panel {
    min-height:440px;
  }

  .message-list {
    min-height:228px;
  }

  .model-dialog-backdrop {
    align-items:end;
    padding:0;
  }

  .model-dialog {
    width:100%;
    max-height:calc(100dvh - env(safe-area-inset-top));
    border-radius:22px 22px 0 0;
  }

  .model-dialog-body {
    overscroll-behavior:contain;
    padding-bottom:22px;
  }

  .model-dialog-footer {
    flex-wrap:wrap;
    padding:12px 18px max(12px, env(safe-area-inset-bottom));
    box-shadow:0 -8px 20px rgba(47,62,37,.07);
  }

  .model-dialog-footer .dialog-primary-button,
  .model-dialog-footer .dialog-secondary-button {
    min-height:44px;
    flex:1 1 126px;
  }

  .selected-provider-banner {
    align-items:flex-start;
  }

  .selected-provider-banner > span:last-child {
    white-space:nowrap;
  }
}

@media (max-width:640px) and (max-height:760px) {
  .career-workspace {
    min-height:0;
  }

  .career-chat-panel {
    min-height:400px;
  }

  .conversation-list {
    max-height:156px;
  }

  .message-list {
    min-height:176px;
  }

  .composer textarea {
    min-height:56px;
  }
}

@container app-main (max-width:1239px) {
  .career-workspace,
  .career-workspace.has-context-rail {
    grid-template-columns:280px minmax(0,1fr);
    gap:10px;
  }

  .career-workspace.history-collapsed,
  .career-workspace.has-context-rail.history-collapsed {
    grid-template-columns:48px minmax(0,1fr);
  }

  .career-history-panel {
    padding:11px;
  }

  .career-history-panel.collapsed {
    padding:8px 5px;
  }

  .chat-header {
    gap:10px;
    padding-right:14px;
    padding-left:14px;
  }

  .message-list {
    padding-right:14px;
    padding-left:14px;
  }

  .compact-panel-triggers {
    display:flex;
    flex:0 0 auto;
    gap:8px;
    margin-left:auto;
  }

  .compact-panel-trigger.history-trigger {
    display:none;
  }

  .context-rail-slot {
    position:fixed;
    z-index:930;
    top:12px;
    right:12px;
    bottom:12px;
    width:min(420px,calc(100vw - 24px));
    height:auto;
    overflow:visible;
    border-radius:20px;
    background:#fff;
    box-shadow:0 24px 72px rgba(25,44,20,.24);
    transform:translateX(calc(100% + 28px));
    transition:transform .2s ease;
  }

  .compact-context-open .context-rail-slot {
    transform:translateX(0);
  }

  .context-rail-resizer {
    display:none;
  }

  .compact-panel-close.context-panel-close {
    position:absolute;
    z-index:40;
    top:10px;
    right:10px;
    display:grid;
    place-items:center;
    font-size:22px;
  }

  .career-panel-scrim {
    position:fixed;
    z-index:920;
    inset:0;
    display:block;
    border:0;
    background:rgba(18,35,18,.3);
    cursor:default;
  }
}

@container app-main (max-width:819px) {
  .career-workspace,
  .career-workspace.has-context-rail,
  .career-workspace.history-collapsed,
  .career-workspace.has-context-rail.history-collapsed {
    height:calc(100dvh - 92px);
    min-height:400px;
    grid-template-columns:minmax(0,1fr);
    grid-template-rows:minmax(0,1fr);
    gap:0;
    overflow:hidden;
  }

  .career-chat-panel {
    height:100%;
    min-height:0;
    grid-row:1;
    grid-column:1;
    overflow:hidden;
  }

  .career-history-panel {
    position:fixed;
    z-index:940;
    top:12px;
    bottom:12px;
    left:12px;
    display:flex;
    width:min(360px,calc(100vw - 24px));
    height:auto;
    max-height:none;
    flex-direction:column;
    overflow:hidden;
    padding:16px;
    box-shadow:0 24px 72px rgba(25,44,20,.24);
    transform:translateX(calc(-100% - 28px));
    transition:transform .2s ease;
  }

  .compact-history-open .career-history-panel {
    transform:translateX(0);
  }

  .compact-panel-close:not(.context-panel-close) {
    position:absolute;
    z-index:10;
    top:10px;
    right:10px;
    display:grid;
    place-items:center;
    font-size:22px;
  }

  .compact-panel-trigger.history-trigger {
    display:block;
  }

  .conversation-list {
    min-height:0;
    max-height:none;
    flex:1;
    overflow:auto;
  }

  .chat-header {
    min-height:60px;
    align-items:center;
    flex-direction:row;
    flex-wrap:wrap;
    padding:10px 12px;
  }

  .chat-header > div:first-child {
    flex:1 1 180px;
  }

  .chat-material-actions {
    width:100%;
    overflow-x:auto;
  }

  .chat-material-button {
    min-width:max-content;
  }

  .message-list {
    min-height:0;
    flex:1;
    overflow:auto;
  }

  .composer {
    position:relative;
    bottom:auto;
  }
}

@media (prefers-reduced-motion:reduce) {
  .career-history-panel,
  .context-rail-slot {
    transition:none;
  }
}
</style>
