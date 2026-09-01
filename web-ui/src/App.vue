<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import CareerAssistantPage from './components/CareerAssistantPage.vue'
import BrowserInterviewMasterPage from './components/BrowserInterviewMasterPage.vue'
import OnlineAssessmentAssistantPage from './components/OnlineAssessmentAssistantPage.vue'
import InterviewLibraryPage from './components/InterviewLibraryPage.vue'
import JobSearchWorkspace from './components/JobSearchWorkspace.vue'
import LoginPage from './components/LoginPage.vue'
import ObservabilityPage from './components/ObservabilityPage.vue'
import AdminConsolePage from './components/AdminConsolePage.vue'
import ManualPipelinePanel from './components/ManualPipelinePanel.vue'
import ThemeSwitcher from './components/ThemeSwitcher.vue'
import ImagePreviewDialog from './components/ImagePreviewDialog.vue'
import { decorateOpenableImages, resolveImagePreviewRequest } from './image-preview.js'
import {
  figureRoleLabel,
  visualSpecSummary,
  visualValidationLabel
} from './article-visual.js'
import {
  DEFAULT_APP_ROUTE,
  buildNavigationFallback,
  canAccessConfiguredFeature,
  canAccessNavigationItem,
  normalizeAppRoute,
  resolveAuthenticatedRoute
} from './navigation-access.js'

const preview = ref(null)
const mediaLibrary = ref({ items: [], pending_video_clips: [], summary: {} })
const executionHistory = ref([])
const executionHistoryLoading = ref(false)
const executionHistoryError = ref('')
const tasks = ref([])
const healthSummary = ref(null)
const loading = ref(false)
const actionLoading = ref('')
const upgradingImages = ref(false)
const errorMessage = ref('')
const actionMessage = ref('')
const imageUpgradeMessage = ref('')
const imagePreviewDialog = ref(null)
const approvalComment = ref('')
const operatorName = ref('content-reviewer')
const mainViewport = ref(null)
const skills = ref([])
const selectedSkill = ref(null)
const installedSkillQuery = ref('')
const skillSearchQuery = ref('')
const skillSearchResults = ref([])
const skillSearchSnapshot = ref(null)
const skillsLoading = ref(false)
const skillSearchLoading = ref(false)
const skillSaving = ref(false)
const skillEditorMarkdown = ref('')
const skillSaveName = ref('')
const skillMessage = ref('')
const skillErrorMessage = ref('')
const starPopoverSkill = ref(null)
const starPopoverPosition = ref({ left: '0px', top: '0px' })
const authReady = ref(false)
const authUser = ref(null)
const mobileNavOpen = ref(false)
const isMobileViewport = ref(false)
const uiTheme = ref(document.documentElement.dataset.uiTheme === 'green' ? 'green' : 'blue')
const navigationModules = ref([])
const navigationReady = ref(false)
const navigationError = ref('')
const accountMenuRef = ref(null)
const accountMenuOpen = ref(false)
const accountMenuSection = ref('')
let starPopoverCloseTimer = null

const vOpenableImages = {
  mounted: decorateOpenableImages,
  updated: decorateOpenableImages
}

function openImagePreview(source, alt = '图片预览') {
  if (!source) return
  imagePreviewDialog.value = { source, alt }
}

function closeImagePreview() {
  imagePreviewDialog.value = null
}

function handleArticleImagePreview(event) {
  const request = resolveImagePreviewRequest(event.target)
  if (!request) return
  event.preventDefault()
  openImagePreview(request.source, request.alt)
}

const accountDisplayName = computed(() => authUser.value?.display_name || authUser.value?.username || '用户')
const accountIdentifier = computed(() => authUser.value?.email || '未绑定')
const accountRoleLabel = computed(() => authUser.value?.role === 'admin' ? '管理员' : '普通用户')
const accountInitial = computed(() => accountDisplayName.value.trim().slice(0, 1).toUpperCase() || 'U')
const navigationModuleMap = computed(() => new Map(navigationModules.value.map((item) => [item.key, item])))
const careerInterviewMasterVisible = computed(() => canAccessConfiguredFeature(
  navigationModuleMap.value.get('career_interview_master'),
  authUser.value?.role
))
const careerOnlineAssessmentVisible = computed(() => canAccessConfiguredFeature(
  navigationModuleMap.value.get('career_online_assessment'),
  authUser.value?.role
))

function setUiTheme(theme) {
  const nextTheme = theme === 'green' ? 'green' : 'blue'
  uiTheme.value = nextTheme
  document.documentElement.dataset.uiTheme = nextTheme
  window.localStorage.setItem('find-job-ui-theme', nextTheme)
}

function closeAccountMenu() {
  accountMenuOpen.value = false
  accountMenuSection.value = ''
}

function toggleAccountMenu() {
  accountMenuOpen.value = !accountMenuOpen.value
  if (!accountMenuOpen.value) accountMenuSection.value = ''
}

function toggleAccountMenuSection(section) {
  accountMenuSection.value = accountMenuSection.value === section ? '' : section
}

function handleAccountThemeChange(theme) {
  setUiTheme(theme)
}

function handleAccountMenuPointerDown(event) {
  if (!accountMenuOpen.value || accountMenuRef.value?.contains(event.target)) return
  closeAccountMenu()
}

const pipelineTaskNames = [
  'StartupSelfCheckTask',
  'SearchTask',
  'SummaryTask',
  'ShortVideoPromptTask',
  'ImageTask',
  'VideoClipPlanTask',
  'SeedanceClipTask',
  'AudioTask',
  'StorageTask',
  'VideoTask',
  'VideoStatusTask',
  'PreviewTask',
  'GitHubImageUpgradeAction',
  'ArticleLayoutTask',
  'DeliverTask',
  'CatTask'
]

// 短视频蓝图仍由后端生成并供视频流水线消费，但当前不作为独立审核页面展示。
// 后续若重新启用人工蓝图审核，只需恢复此开关，不必重建页面和数据链路。
const isStoryboardReviewVisible = false

const routeItems = [
  { path: '/review', label: '工作台', description: '公众号审核总览' },
  { path: '/review/article', label: '文章预览', description: '只读审核' },
  { path: '/review/pipeline', label: '任务流程', description: '运行状态' },
  { path: '/review/assets', label: '媒体素材', description: '图片音视频' },
  { path: '/review/history', label: '执行历史', description: '推文与素材归档' },
  ...(isStoryboardReviewVisible
    ? [{ path: '/review/storyboard', label: '短视频蓝图', description: '分镜规划' }]
    : []),
  { path: '/review/prompts', label: '生成方案', description: '图片方案与视频提示词' }
]

const appNavItems = [
  { moduleKey: 'career_assistant', label: '求职助手', icon: '◉', path: '/career', enabled: true },
  { moduleKey: 'workbench', label: '工作台', icon: '▣', path: '/review', enabled: true },
  { moduleKey: 'interview_library', label: '面经库', icon: '⌘', path: '/interviews', enabled: true },
  { moduleKey: 'job_library', label: '职位库', icon: '⌕', path: '/interviews/jobs', enabled: true },
  { moduleKey: 'skill_library', label: '技能库', icon: '✦', path: '/skills', enabled: true },
  { moduleKey: 'langsmith', label: 'LangSmith', icon: '◌', path: '/observability', enabled: true, requiredRole: 'admin' },
  { moduleKey: 'admin_console', label: '管理台', icon: '⚙', path: '/admin/modules', enabled: true, requiredRole: 'admin' },
]

const currentRoute = ref(normalizeRoute(window.location.pathname))
const visibleAppNavItems = computed(() => appNavItems.filter(canAccessNavItem))
const firstAccessibleRoute = computed(() => visibleAppNavItems.value[0]?.path ?? '')
const canAccessCurrentRoute = computed(() => canAccessRoute(currentRoute.value))

const content = computed(() => preview.value?.content ?? null)
const articleLayout = computed(() => preview.value?.article_layout ?? null)
const approval = computed(() => preview.value?.approval ?? null)
const mediaAssets = computed(() => preview.value?.media_assets ?? [])
const activeContentId = computed(() => normalizeContentId(content.value?.id))
const mediaLibraryAssetTypes = new Set(['image', 'audio', 'video', 'video_clip'])
const mediaLibraryAssets = computed(() => {
  const contentId = activeContentId.value
  if (!contentId) return []
  return (mediaLibrary.value?.items ?? []).filter((asset) => (
    normalizeContentId(asset?.content_id) === contentId
    && mediaLibraryAssetTypes.has(asset?.asset_type)
  ))
})
const pendingVideoClips = computed(() => {
  const contentId = activeContentId.value
  if (!contentId) return []
  return (mediaLibrary.value?.pending_video_clips ?? []).filter(
    (clip) => normalizeContentId(clip?.content_id) === contentId
  )
})
const mediaLibrarySummary = computed(() => {
  const assets = mediaLibraryAssets.value
  const pendingClips = pendingVideoClips.value
  return {
    total_asset_count: assets.length,
    image_count: assets.filter((asset) => asset.asset_type === 'image').length,
    audio_count: assets.filter((asset) => asset.asset_type === 'audio').length,
    video_count: assets.filter((asset) => ['video', 'video_clip'].includes(asset.asset_type)).length,
    pending_video_count: pendingClips.length,
    failed_asset_count: assets.filter((asset) => asset.status === 'failed').length
  }
})
const videoStoryboard = computed(() => preview.value?.video_storyboard ?? null)
const videoClipPlans = computed(() => preview.value?.video_clip_plans ?? [])
const imagePrompts = computed(() => content.value?.image_prompts ?? [])
const requiredImageCount = computed(() => Math.max(1, imagePrompts.value.length))

const imageAssets = computed(() => mediaAssets.value.filter((asset) => asset.asset_type === 'image'))
const githubImageAssets = computed(() =>
  imageAssets.value.filter((asset) => asset.provider === 'github_repository_asset')
)
const audioAssets = computed(() => mediaAssets.value.filter((asset) => asset.asset_type === 'audio'))
const videoAssets = computed(() =>
  mediaAssets.value.filter((asset) => ['video', 'video_clip'].includes(asset.asset_type))
)
const overviewImageAssets = computed(() => imageAssets.value.slice(0, requiredImageCount.value))
const articleExcerpt = computed(() => buildArticleExcerpt(content.value?.article_markdown ?? content.value?.digest ?? ''))
const wechatTitle = computed(() => {
  return articleLayout.value?.title
    ?? content.value?.title
    ?? articleLayout.value?.wechat_title
    ?? content.value?.wechat_title
    ?? 'GitHub 技术周报'
})

const activeRoute = computed(() => {
  if (isAuthRoute(currentRoute.value)) {
    return { path: '/login', label: '登录', description: '平台账号访问' }
  }
  if (currentRoute.value === '/career') {
    return { path: '/career', label: '求职助手', description: '简历匹配与职业咨询' }
  }
  if (currentRoute.value === '/career/online-assessment') {
    return { path: '/career/online-assessment', label: '线上笔试助手', description: '识别题面、生成代码并运行可见测试' }
  }
  if (currentRoute.value === '/interviews/jobs') {
    return { path: '/interviews/jobs', label: '职位库', description: '输入岗位名称获取当前在招职位' }
  }
  if (currentRoute.value === '/interviews') {
    return { path: '/interviews', label: '面经库', description: '结构化面经与检索增强问答' }
  }
  if (currentRoute.value === '/skills') {
    return { path: '/skills', label: '技能库', description: '查找、查看和维护本地 Skill' }
  }
  if (currentRoute.value === '/observability') {
    return { path: '/observability', label: '可观测性', description: 'LangSmith 模型链路监控' }
  }
  if (currentRoute.value.startsWith('/admin/')) {
    return { path: currentRoute.value, label: '管理台', description: '平台模块与内容工作流配置' }
  }
  return routeItems.find((item) => item.path === currentRoute.value) ?? routeItems[0]
})

const selectedSkillId = computed(() => selectedSkill.value?.id ?? '')
const filteredInstalledSkills = computed(() => {
  const query = installedSkillQuery.value.trim().toLowerCase()
  if (!query) return skills.value
  return skills.value.filter((skill) => {
    const haystack = [
      skill.name,
      skill.description,
      skill.description_zh,
      skill.source_label,
      skill.repository_full_name,
      skill.path_hint
    ].join(' ').toLowerCase()
    return haystack.includes(query)
  })
})

const taskCards = computed(() => {
  return pipelineTaskNames.map((taskName) => {
    const latest = tasks.value.find((item) => item.task_name === taskName)
    return {
      taskName,
      status: latest?.status ?? 'pending',
      runId: latest?.run_id ?? '',
      errorMessage: latest?.error_message ?? '',
      metadata: latest?.metadata ?? {}
    }
  })
})

const lifecycleStages = computed(() => {
  const summaryReady = Boolean(content.value)
  const imageReady = imageAssets.value.length >= requiredImageCount.value
  const audioReady = audioAssets.value.length >= 1
  const videoReady = videoAssets.value.length >= 1
  const approvalReady = approval.value?.decision === 'approved'

  return [
    {
      key: 'content',
      label: '内容',
      status: summaryReady ? '已就绪' : '未就绪',
      detail: summaryReady ? `content_id=${content.value.id}` : '等待内容生成',
      ready: summaryReady
    },
    {
      key: 'image',
      label: '图片',
      status: imageReady ? '已就绪' : '未就绪',
      detail: `${imageAssets.value.length}/${requiredImageCount.value} 张`,
      ready: imageReady
    },
    {
      key: 'audio',
      label: '音频',
      status: audioReady ? '已就绪' : '未就绪',
      detail: `${audioAssets.value.length}/1 条`,
      ready: audioReady
    },
    {
      key: 'video',
      label: '视频',
      status: videoReady ? '已就绪' : '未就绪',
      detail: `${videoAssets.value.length}/1 个`,
      ready: videoReady
    },
    {
      key: 'approval',
      label: '审核',
      status: approvalReady ? '已通过' : '待确认',
      detail: approvalReady ? '可推送草稿箱' : '等待人工确认',
      ready: approvalReady
    }
  ]
})

const completedLifecycleStageCount = computed(() => lifecycleStages.value.filter((stage) => stage.ready).length)

const blockingReasons = computed(() => healthSummary.value?.top_blocking_reasons ?? [])
const blockingItems = computed(() => healthSummary.value?.blocking_items ?? [])

const moduleCards = computed(() => [
  {
    path: '/review/article',
    kind: 'article',
    title: '文章预览',
    description: content.value?.title ?? '等待内容生成',
    accent: 'blue'
  },
  {
    path: '/review/pipeline',
    kind: 'pipeline',
    title: '任务流程',
    description: `${taskCards.value.filter((task) => task.status === 'succeeded').length}/${taskCards.value.length} 个任务成功`,
    accent: 'green'
  },
  {
    path: '/review/assets',
    kind: 'assets',
    title: '媒体素材',
    description: `图片 ${imageAssets.value.length}，音频 ${audioAssets.value.length}，视频 ${videoAssets.value.length}`,
    accent: 'orange'
  },
  {
    path: '/review/history',
    kind: 'archive',
    title: '执行历史',
    description: '按 content_id 保留推文与本次生成的专属媒体素材',
    accent: 'green'
  },
  {
    path: '/review/prompts',
    kind: 'prompts',
    title: '生成方案',
    description: `图片总结方案 ${imagePrompts.value.length} 条，视频片段 ${videoClipPlans.value.length} 条`,
    accent: 'blue'
  }
])

function normalizeContentId(value) {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function contentIdFromLocation() {
  return normalizeContentId(new URLSearchParams(window.location.search).get('content_id'))
}

function formatArchiveTimestamp(value) {
  if (!value) return '生成时间未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return `生成于 ${String(value)}`

  const parts = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).formatToParts(date)
  const partMap = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `生成于 ${partMap.year}-${partMap.month}-${partMap.day} ${partMap.hour}:${partMap.minute}`
}

function normalizeRoute(pathname) {
  return normalizeAppRoute(pathname, routeItems.map((item) => item.path))
}

function isAuthRoute(route) {
  return route === '/login' || route === '/register' || route === '/forgot-password'
}

async function navigateTo(path) {
  closeMobileNavigation()
  const nextUrl = new URL(path, window.location.origin)
  const nextRoute = normalizeRoute(nextUrl.pathname)
  if (!canAccessRoute(nextRoute)) return
  const nextLocation = `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`
  const currentLocation = `${window.location.pathname}${window.location.search}${window.location.hash}`
  if (currentRoute.value === nextRoute && currentLocation === nextLocation) return
  window.history.pushState({}, '', nextLocation)
  currentRoute.value = nextRoute
  if (currentRoute.value === '/skills' && !skills.value.length) {
    await loadSkills()
  }
  if (currentRoute.value === '/review/history') {
    await loadExecutionHistory()
  } else if (currentRoute.value.startsWith('/review')) {
    await refreshDashboard()
  }
  mainViewport.value?.scrollTo({ top: 0, behavior: 'smooth' })
}

function handlePopState() {
  closeMobileNavigation()
  currentRoute.value = normalizeRoute(window.location.pathname)
  if (!canAccessRoute(currentRoute.value)) {
    if (ensureCurrentRouteAccess()) void refreshCurrentPage()
    return
  }
  if (currentRoute.value === '/skills' && !skills.value.length) {
    void loadSkills()
  }
  if (currentRoute.value === '/review/history') {
    void loadExecutionHistory()
  } else if (currentRoute.value.startsWith('/review')) {
    void refreshDashboard()
  }
}

function closeMobileNavigation() {
  mobileNavOpen.value = false
}

function toggleMobileNavigation() {
  if (!isMobileViewport.value) return
  mobileNavOpen.value = !mobileNavOpen.value
}

function syncMobileViewport() {
  isMobileViewport.value = window.matchMedia('(max-width: 1023.98px)').matches
  if (!isMobileViewport.value) closeMobileNavigation()
}

function handleViewportResize() {
  closeStarPopover()
  syncMobileViewport()
}

function handleGlobalKeydown(event) {
  if (event.key === 'Escape') {
    closeMobileNavigation()
    closeStarPopover()
    closeAccountMenu()
  }
}

function isAppNavActive(item) {
  if (!item.path) return false
  if (item.path === '/review') return currentRoute.value.startsWith('/review')
  if (item.moduleKey === 'career_assistant') return currentRoute.value.startsWith('/career')
  if (item.moduleKey === 'admin_console') return currentRoute.value.startsWith('/admin/')
  return currentRoute.value === item.path
}

function canAccessNavItem(item) {
  const configuredModule = navigationModuleMap.value.get(item.moduleKey)
  return canAccessNavigationItem(item, configuredModule, authUser.value?.role)
}

function navItemForRoute(route) {
  if (route.startsWith('/review')) return appNavItems.find((item) => item.moduleKey === 'workbench')
  if (route === '/career/interview-master') return appNavItems.find((item) => item.moduleKey === 'career_assistant')
  if (route === '/career/online-assessment') return appNavItems.find((item) => item.moduleKey === 'career_assistant')
  if (route === '/interviews/jobs') return appNavItems.find((item) => item.moduleKey === 'job_library')
  if (route === '/interviews') return appNavItems.find((item) => item.moduleKey === 'interview_library')
  if (route.startsWith('/admin/')) return appNavItems.find((item) => item.moduleKey === 'admin_console')
  return appNavItems.find((item) => item.path === route)
}

function canAccessRoute(route) {
  if (isAuthRoute(route)) return true
  const navItem = navItemForRoute(route)
  if (!navItem || !canAccessNavItem(navItem)) return false
  if (route === '/career/interview-master') return careerInterviewMasterVisible.value
  if (route === '/career/online-assessment') return careerOnlineAssessmentVisible.value
  return true
}

function ensureCurrentRouteAccess() {
  if (canAccessRoute(currentRoute.value)) return true
  const fallback = firstAccessibleRoute.value
  if (!fallback) return false
  window.history.replaceState({}, '', fallback)
  currentRoute.value = fallback
  return true
}

function applyNavigationConfig(items) {
  if (!Array.isArray(items) || !items.length) {
    applyNavigationFallback()
    return
  }
  navigationModules.value = items
  ensureCurrentRouteAccess()
}

function applyNavigationFallback() {
  navigationModules.value = buildNavigationFallback(appNavItems, authUser.value?.role)
  ensureCurrentRouteAccess()
}

async function loadNavigationConfig() {
  navigationReady.value = false
  navigationError.value = ''
  try {
    const response = await fetch('/api/navigation/modules', { credentials: 'include', cache: 'no-store' })
    if (!response.ok) throw new Error('无法读取路由模块配置')
    applyNavigationConfig((await response.json()).items)
  } catch {
    navigationError.value = '无法读取可用模块，请检查网络后重试。'
    // 读取失败时保持拒绝访问；错误状态会提供明确提示与重试入口。
    applyNavigationFallback()
  } finally {
    navigationReady.value = true
  }
}

async function retryNavigationConfig() {
  await loadNavigationConfig()
  if (!navigationError.value) await refreshCurrentPage()
}

async function loadCurrentUser() {
  try {
    const response = await fetch('/api/auth/me', { credentials: 'include', cache: 'no-store' })
    if (!response.ok) {
      authUser.value = null
      return
    }
    const payload = await response.json()
    authUser.value = payload.user ?? null
    if (authUser.value) {
      const authenticatedRoute = resolveAuthenticatedRoute(currentRoute.value)
      if (authenticatedRoute !== currentRoute.value) {
        window.history.replaceState({}, '', authenticatedRoute)
        currentRoute.value = authenticatedRoute
      }
    }
  } catch {
    authUser.value = null
  } finally {
    authReady.value = true
  }
}

async function handleAuthenticated(user) {
  authUser.value = user
  authReady.value = true
  const nextPath = resolveAuthenticatedRoute(currentRoute.value)
  window.history.replaceState({}, '', nextPath)
  currentRoute.value = nextPath
  await loadNavigationConfig()
  ensureCurrentRouteAccess()
  await refreshCurrentPage()
}

async function logoutPlatform() {
  closeAccountMenu()
  try {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
  } finally {
    authUser.value = null
    navigationModules.value = []
    navigationReady.value = false
    navigationError.value = ''
    window.history.replaceState({}, '', '/login')
    currentRoute.value = '/login'
  }
}

async function refreshCurrentPage() {
  if (!canAccessCurrentRoute.value) return
  if (currentRoute.value === '/career' || currentRoute.value.startsWith('/interviews') || currentRoute.value.startsWith('/admin/')) return
  if (currentRoute.value === '/skills') {
    await loadSkills(selectedSkillId.value)
    return
  }
  if (currentRoute.value === '/review/history') {
    await loadExecutionHistory()
    return
  }
  await refreshDashboard()
}

async function refreshDashboard() {
  loading.value = true
  errorMessage.value = ''

  try {
    const cacheBuster = Date.now()
    const requestedContentId = ['/review/assets', '/review/article'].includes(currentRoute.value)
      ? contentIdFromLocation()
      : null
    const previewEndpoint = requestedContentId
      ? `/api/execution-history/${requestedContentId}?_=${cacheBuster}`
      : `/api/preview/latest?_=${cacheBuster}`
    const [previewResponse, tasksResponse, healthResponse] = await Promise.all([
      fetch(previewEndpoint, { cache: 'no-store' }),
      fetch(`/api/tasks/recent?limit=80&_=${cacheBuster}`, { cache: 'no-store' }),
      fetch(`/api/system/health-summary?_=${cacheBuster}`, { cache: 'no-store' })
    ])

    if (!previewResponse.ok) throw new Error(`预览接口异常：${previewResponse.status}`)
    if (!tasksResponse.ok) throw new Error(`任务接口异常：${tasksResponse.status}`)
    if (!healthResponse.ok) throw new Error(`健康摘要接口异常：${healthResponse.status}`)

    preview.value = normalizePreviewPayload(await previewResponse.json())
    const contentId = normalizeContentId(preview.value?.content?.id)
    if (contentId) {
      const mediaLibraryResponse = await fetch(
        `/api/media-assets?content_id=${contentId}&limit=300&_=${cacheBuster}`,
        { cache: 'no-store' }
      )
      const mediaPayload = await mediaLibraryResponse.json().catch(() => ({}))
      if (!mediaLibraryResponse.ok) {
        throw new Error(mediaPayload.detail ?? `媒体资源库接口异常：${mediaLibraryResponse.status}`)
      }
      mediaLibrary.value = mediaPayload
    } else {
      mediaLibrary.value = { items: [], pending_video_clips: [], summary: {} }
    }
    const taskPayload = await tasksResponse.json()
    tasks.value = taskPayload.items ?? []
    healthSummary.value = await healthResponse.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '刷新失败'
  } finally {
    loading.value = false
  }
}

async function loadExecutionHistory() {
  executionHistoryLoading.value = true
  executionHistoryError.value = ''

  try {
    const response = await fetch(`/api/execution-history?limit=80&_=${Date.now()}`, { cache: 'no-store' })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail ?? `执行历史接口异常：${response.status}`)
    }
    executionHistory.value = payload.items ?? []
  } catch (error) {
    executionHistoryError.value = error instanceof Error ? error.message : '执行历史读取失败'
    executionHistory.value = []
  } finally {
    executionHistoryLoading.value = false
  }
}

function normalizePreviewPayload(payload) {
  const articleHtml = payload?.article_layout?.article_html
  if (typeof articleHtml !== 'string') return payload

  // 公众号发布前保留 wechat-image-asset 协议，由 DeliverTask 统一替换为微信素材 URL。
  // 浏览器预览无法解析该协议，因此只在前端预览中映射到媒体文件接口。
  payload.article_layout.article_html = articleHtml.replace(
    /src=(["'])wechat-image-asset:\/\/(\d+)\1/g,
    (_matched, quote, assetId) => `src=${quote}/api/media-assets/${assetId}/file${quote}`
  )
  return payload
}

async function loadSkills(preferredSkillId = '') {
  skillsLoading.value = true
  skillErrorMessage.value = ''

  try {
    const response = await fetch('/api/skills')
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail ?? `技能列表接口异常：${response.status}`)
    }

    skills.value = payload.items ?? []
    const preferredSkill =
      skills.value.find((skill) => skill.id === preferredSkillId) ??
      skills.value.find((skill) => skill.name === 'find-skills') ??
      skills.value[0]

    if (preferredSkill) {
      await loadSkillDetail(preferredSkill.id)
    } else {
      selectedSkill.value = null
      skillEditorMarkdown.value = ''
      skillSaveName.value = ''
    }
  } catch (error) {
    skillErrorMessage.value = error instanceof Error ? error.message : '技能列表加载失败'
  } finally {
    skillsLoading.value = false
  }
}

async function loadSkillDetail(skillId) {
  if (!skillId) return
  skillErrorMessage.value = ''
  skillMessage.value = ''

  try {
    const response = await fetch(`/api/skills/${encodeURIComponent(skillId)}`)
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail ?? `Skill.md 读取失败：${response.status}`)
    }

    selectedSkill.value = payload.skill
    skillEditorMarkdown.value = payload.markdown ?? ''
    skillSaveName.value = payload.skill?.name ?? ''
  } catch (error) {
    skillErrorMessage.value = error instanceof Error ? error.message : 'Skill.md 读取失败'
  }
}

async function searchSkills(forceRefresh = false) {
  skillSearchLoading.value = true
  skillErrorMessage.value = ''
  skillMessage.value = forceRefresh
    ? '正在刷新 GitHub 开放 Skill 快照…'
    : '正在读取关键词快照；首次搜索会请求 GitHub。'

  try {
    const response = await fetch('/api/skills/search', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        query: skillSearchQuery.value,
        force_refresh: forceRefresh
      })
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail ?? `技能搜索接口异常：${response.status}`)
    }

    skillSearchResults.value = payload.items ?? []
    skillSearchSnapshot.value = {
      snapshotAt: payload.snapshot_at ?? null,
      cacheState: payload.cache_state ?? 'local',
      dataSource: payload.data_source ?? 'local_installed'
    }
    const elapsed = Number.isFinite(payload.elapsed_ms) ? `（${(payload.elapsed_ms / 1000).toFixed(1)} 秒）` : ''
    if (payload.fallback_reason) {
      skillMessage.value = `${payload.fallback_reason}${elapsed}`
    } else if (payload.status_message) {
      skillMessage.value = `${payload.status_message}${elapsed}`
    } else if (payload.used_llm) {
      skillMessage.value = `已使用 ${payload.model ?? 'DS4Pro'} 改写查询，并搜索 GitHub Skill：${payload.normalized_query || skillSearchQuery.value}`
    } else {
      skillMessage.value = payload.search_scope === 'github_open_skills'
        ? '已搜索 GitHub 开放 Skill'
        : '已展示本地已安装 Skill'
    }
  } catch (error) {
    skillErrorMessage.value = error instanceof Error ? error.message : '技能搜索失败'
  } finally {
    skillSearchLoading.value = false
  }
}

function skillSnapshotSourceLabel(source) {
  return ({
    github_live: 'GitHub 实时结果',
    github_snapshot: 'GitHub 周期快照',
    local_installed: '本地已安装 Skill'
  })[source] ?? '本地结果'
}

function skillSnapshotStateLabel(state) {
  return ({
    live: '刚刚刷新',
    fresh: '快照有效期内',
    stale: '历史快照',
    local: '本地数据'
  })[state] ?? '快照数据'
}

function skillSnapshotTime(value) {
  if (!value) return '暂无 GitHub 快照时间'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return `更新于 ${date.toLocaleString('zh-CN', { hour12: false })}`
}

async function selectSearchResult(result) {
  if (!result?.skill) return
  if (result.markdown) {
    selectedSkill.value = result.skill
    skillEditorMarkdown.value = result.markdown
    skillSaveName.value = result.skill.name
    skillMessage.value = `已加载 GitHub Skill：${result.skill.name}`
    skillErrorMessage.value = ''
    return
  }
  await loadSkillDetail(result.skill.id)
}

async function saveSkill() {
  skillSaving.value = true
  skillErrorMessage.value = ''
  skillMessage.value = ''

  try {
    const response = await fetch('/api/skills/save', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        name: skillSaveName.value,
        markdown: skillEditorMarkdown.value,
        source_repository_full_name: selectedSkill.value?.repository_full_name ?? null,
        source_homepage_url: selectedSkill.value?.homepage_url ?? null,
        source_author: selectedSkill.value?.author ?? null
      })
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail ?? `Skill 保存失败：${response.status}`)
    }

    selectedSkill.value = payload.skill
    skillEditorMarkdown.value = payload.markdown ?? skillEditorMarkdown.value
    skillSaveName.value = payload.skill?.name ?? skillSaveName.value
    skillMessage.value = payload.created
      ? `已新增本地 Skill：${payload.skill?.name}`
      : `已更新本地 Skill：${payload.skill?.name}`
    await loadSkills(payload.skill?.id)
  } catch (error) {
    skillErrorMessage.value = error instanceof Error ? error.message : 'Skill 保存失败'
  } finally {
    skillSaving.value = false
  }
}

async function submitReviewAction(decision) {
  if (!content.value?.id) {
    errorMessage.value = '当前没有可审核内容'
    return
  }

  if (decision === 'approved') {
    const confirmed = window.confirm('通过后会立即尝试创建微信公众号草稿，确认继续吗？')
    if (!confirmed) return
  }

  actionLoading.value = decision
  errorMessage.value = ''
  actionMessage.value = ''

  try {
    const response = await fetch('/api/review/content-action', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        content_id: content.value.id,
        decision,
        operator: operatorName.value,
        comment: approvalComment.value
      })
    })

    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail ?? `审核动作接口异常：${response.status}`)
    }

    approvalComment.value = ''
    const taskNames = (payload.task_results ?? []).map((item) => item.task_name).join(' → ')
    actionMessage.value = taskNames ? `已触发任务链：${taskNames}` : '审核动作已提交'
    await refreshDashboard()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '审核动作提交失败'
  } finally {
    actionLoading.value = ''
  }
}

async function upgradeGithubImages() {
  if (!content.value?.id) {
    errorMessage.value = '当前没有可升级图片的内容'
    return
  }

  upgradingImages.value = true
  errorMessage.value = ''
  imageUpgradeMessage.value = ''

  try {
    const response = await fetch(`/api/content/${content.value.id}/github-image-upgrade`, {
      method: 'POST'
    })

    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail ?? `GitHub 仓库图升级接口异常：${response.status}`)
    }

    const result = payload.result ?? {}
    imageUpgradeMessage.value = [
      `新增 ${result.created_count ?? 0} 张仓库图`,
      `替换旧图 ${result.replaced_count ?? 0} 张`,
      `已有跳过 ${result.skipped_existing_count ?? 0} 个项目`,
      `未找到 ${result.not_found_count ?? 0} 个项目`,
      `失败 ${result.failed_count ?? 0} 个项目`
    ].join('；')
    await refreshDashboard()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'GitHub 仓库图升级失败'
  } finally {
    upgradingImages.value = false
  }
}

function statusText(status) {
  const mapping = {
    succeeded: '成功',
    failed: '失败',
    running: '运行中',
    created: '已创建',
    pending: '等待',
    generated: '已生成',
    approved: '已通过审核',
    rejected: '已驳回',
    delivered: '已推送',
    published: '已发布'
  }
  return mapping[status] ?? status
}

function statusClass(status) {
  if (status === 'succeeded') return 'is-success'
  if (status === 'failed') return 'is-danger'
  if (status === 'running') return 'is-running'
  return 'is-muted'
}

function healthText(status) {
  const mapping = {
    healthy: '健康',
    blocked_by_requirements: '缺少资源',
    failed_recently: '最近失败',
    unknown: '未知'
  }
  return mapping[status] ?? status
}

function healthClass(status) {
  if (status === 'healthy') return 'is-success'
  if (status === 'failed_recently') return 'is-danger'
  if (status === 'blocked_by_requirements') return 'is-running'
  return 'is-muted'
}

function mediaTypeLabel(assetType) {
  const mapping = {
    image: '图片',
    audio: '音频',
    video: '视频',
    video_clip: '视频片段',
    video_task: '视频任务',
    video_clip_task: '分段视频任务',
    video_clip_plan: '待生成视频片段'
  }
  return mapping[assetType] ?? assetType
}

function mediaStatusText(status) {
  const mapping = {
    created: '已生成',
    uploaded: '已保存',
    planned: '等待提交生成',
    submitted: '已提交生成',
    processing: '生成中',
    completed: '已完成',
    failed: '生成失败',
    replaced: '已替换'
  }
  return mapping[status] ?? (status || '状态未知')
}

function isPlayableVideoAsset(asset) {
  return ['video', 'video_clip'].includes(asset?.asset_type) && Boolean(asset?.preview_url)
}

function mediaFailureReason(asset) {
  const metadata = asset?.metadata ?? {}
  const value = metadata.error_message ?? metadata.error ?? metadata.failure_reason ?? metadata.last_error
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function approvalText(decision) {
  const mapping = {
    approved: '已通过',
    rejected: '已驳回',
    regenerate_requested: '请求重生成'
  }
  return mapping[decision] ?? '待审核'
}

function approvalClass(decision) {
  if (decision === 'approved') return 'is-success'
  if (decision === 'rejected') return 'is-danger'
  if (decision === 'regenerate_requested') return 'is-running'
  return 'is-muted'
}

function taskNote(task) {
  if (task.errorMessage) return task.errorMessage
  if (task.metadata?.skip_reason) return task.metadata.skip_reason
  if (task.metadata?.missing_requirements?.length) {
    return `缺少：${task.metadata.missing_requirements.join('、')}`
  }
  if (task.metadata?.checks?.missing_requirements?.length) {
    return `缺少：${task.metadata.checks.missing_requirements.join('、')}`
  }
  return ''
}

function previewText(value, fallback = '暂无内容') {
  return value?.trim?.() ? value : fallback
}

function shortText(value, maxLength = 110) {
  const text = String(value ?? '').trim()
  if (text.length <= maxLength) return text
  return `${text.slice(0, maxLength)}…`
}

function buildArticleExcerpt(value) {
  const plainText = String(value ?? '')
    .replace(/```[\s\S]*?```/g, '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/[>*_`]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  return shortText(plainText || '等待文章内容生成', 132)
}

function promptPreviewText(prompt) {
  if (prompt?.prompt_stage === 'deterministic_rendered') {
    return shortText(visualSpecSummary(prompt.visual_spec), 82)
  }
  return shortText(
    prompt?.summary_text
      ?? prompt?.project_summary_text
      ?? prompt?.visual_title
      ?? prompt?.prompt
      ?? '等待图像提示词生成',
    82
  )
}

function visualTakeaways(prompt) {
  const takeaways = prompt?.visual_spec?.takeaways
  if (!Array.isArray(takeaways)) return []
  return takeaways
    .filter((item) => typeof item === 'string' && item.trim())
    .slice(0, 3)
}

function shortRenderKey(value) {
  const renderKey = typeof value === 'string' ? value.trim() : ''
  return renderKey ? renderKey.slice(0, 12) : '未提供'
}

function visualValidationClass(result) {
  if (result?.status === 'passed') return 'is-success'
  if (result?.status === 'failed') return 'is-danger'
  return 'is-muted'
}

function skillDescription(skill) {
  return skill?.description_zh || skill?.description || '暂无中文简介'
}

function formatStars(value) {
  if (typeof value !== 'number') return '暂无 Star'
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}m Stars`
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k Stars`
  return `${value} Stars`
}

function weeklyStarDelta(skill) {
  return typeof skill?.star_delta === 'number' ? skill.star_delta : null
}

function formatWeeklyStarDelta(skill) {
  const delta = weeklyStarDelta(skill)
  if (delta === null) return '等待周快照'
  return `${delta > 0 ? '+' : ''}${delta} Stars`
}

function starGrowthText(skill) {
  if (!skill?.repository_full_name) return '未识别 GitHub 仓库，暂无 Star 数据'
  const current = formatStars(skill.stars)
  if (typeof skill.previous_stars !== 'number' || typeof skill.star_delta !== 'number') {
    return `${skill.repository_full_name} 当前 ${current}；暂无上期快照，7 天后展示环比增长`
  }
  const sign = skill.star_delta >= 0 ? '+' : ''
  const rate = typeof skill.star_growth_rate === 'number'
    ? `，环比 ${sign}${(skill.star_growth_rate * 100).toFixed(2)}%`
    : ''
  return `${skill.repository_full_name} 当前 ${current}；上期 ${formatStars(skill.previous_stars)}；增长 ${sign}${skill.star_delta}${rate}`
}

function starRingStyle(skill) {
  const total = typeof skill?.stars === 'number' ? skill.stars : 0
  const delta = weeklyStarDelta(skill)
  const percent = total > 0 && delta !== null
    ? Math.min(100, Math.abs(delta / total) * 100)
    : 0

  return {
    '--star-ring-percent': `${percent}%`,
    '--star-ring-color': delta !== null && delta < 0 ? '#c45c5c' : '#8aa83f'
  }
}

function starRingLabel(skill) {
  const total = typeof skill?.stars === 'number' ? skill.stars : 0
  const delta = weeklyStarDelta(skill)
  if (total <= 0 || delta === null) return '—'
  return `${Math.abs(delta / total * 100).toFixed(2)}%`
}

function openStarPopover(skill, event) {
  if (!skill || !event?.currentTarget) return
  if (starPopoverCloseTimer) {
    window.clearTimeout(starPopoverCloseTimer)
    starPopoverCloseTimer = null
  }

  const rect = event.currentTarget.getBoundingClientRect()
  const cardWidth = 296
  const cardHeight = 162
  const viewportPadding = 16
  let left = rect.right + 14

  if (left + cardWidth > window.innerWidth - viewportPadding) {
    left = rect.left - cardWidth - 14
  }
  left = Math.max(viewportPadding, left)

  const top = Math.max(
    viewportPadding,
    Math.min(rect.top - 10, window.innerHeight - cardHeight - viewportPadding)
  )

  starPopoverSkill.value = skill
  starPopoverPosition.value = {
    left: `${left}px`,
    top: `${top}px`
  }
}

function toggleStarPopover(skill, event) {
  if (starPopoverSkill.value?.id === skill?.id) {
    closeStarPopover()
    return
  }
  openStarPopover(skill, event)
}

function scheduleStarPopoverClose() {
  if (starPopoverCloseTimer) window.clearTimeout(starPopoverCloseTimer)
  starPopoverCloseTimer = window.setTimeout(() => {
    starPopoverSkill.value = null
    starPopoverCloseTimer = null
  }, 160)
}

function keepStarPopoverOpen() {
  if (starPopoverCloseTimer) {
    window.clearTimeout(starPopoverCloseTimer)
    starPopoverCloseTimer = null
  }
}

function closeStarPopover() {
  if (starPopoverCloseTimer) window.clearTimeout(starPopoverCloseTimer)
  starPopoverCloseTimer = null
  starPopoverSkill.value = null
}

function skillLinkText(skill) {
  if (!skill) return '未选择 Skill'
  return skill.homepage_url || skill.path_hint || '暂无链接'
}

function isExternalSkillLink(skill) {
  return Boolean(skill?.homepage_url?.startsWith?.('http://') || skill?.homepage_url?.startsWith?.('https://'))
}

onMounted(async () => {
  window.addEventListener('popstate', handlePopState)
  window.addEventListener('resize', handleViewportResize)
  window.addEventListener('keydown', handleGlobalKeydown)
  document.addEventListener('pointerdown', handleAccountMenuPointerDown)
  syncMobileViewport()
  if (window.location.pathname === '/' && authUser.value) {
    window.history.replaceState({}, '', DEFAULT_APP_ROUTE)
  }
  await loadCurrentUser()
  if (!authUser.value) {
    if (!isAuthRoute(currentRoute.value)) {
      window.history.replaceState({}, '', '/login')
      currentRoute.value = '/login'
    }
    return
  }
  if (window.location.pathname === '/') {
    window.history.replaceState({}, '', DEFAULT_APP_ROUTE)
    currentRoute.value = DEFAULT_APP_ROUTE
  }
  if (window.location.pathname === '/admin') {
    window.history.replaceState({}, '', '/admin/modules')
    currentRoute.value = '/admin/modules'
  }
  await loadNavigationConfig()
  ensureCurrentRouteAccess()
  if (currentRoute.value !== '/career' && currentRoute.value !== '/interviews' && currentRoute.value !== '/observability' && !currentRoute.value.startsWith('/admin/')) {
    await refreshCurrentPage()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('popstate', handlePopState)
  window.removeEventListener('resize', handleViewportResize)
  window.removeEventListener('keydown', handleGlobalKeydown)
  document.removeEventListener('pointerdown', handleAccountMenuPointerDown)
  closeStarPopover()
})
</script>

<template>
  <template v-if="!authReady || !authUser">
    <LoginPage @authenticated="handleAuthenticated" />
  </template>

  <section v-else-if="!navigationReady" class="platform-route-loading" aria-live="polite">
    <span aria-hidden="true"></span>
    <p>正在加载可用模块…</p>
  </section>

  <section v-else-if="navigationError" class="route-access-state" role="alert">
    <span aria-hidden="true">!</span>
    <h2>无法加载可用模块</h2>
    <p>{{ navigationError }}</p>
    <button type="button" class="secondary-button" @click="retryNavigationConfig">重新加载模块</button>
  </section>

  <BrowserInterviewMasterPage v-else-if="currentRoute === '/career/interview-master' && canAccessCurrentRoute" />

  <OnlineAssessmentAssistantPage v-else-if="currentRoute === '/career/online-assessment' && canAccessCurrentRoute" />

  <div v-else class="shell" :class="{ 'mobile-nav-open': mobileNavOpen }">
    <button
      type="button"
      class="mobile-nav-scrim"
      aria-label="关闭导航"
      @click="closeMobileNavigation"
    ></button>

    <aside
      id="app-navigation"
      class="sidebar"
      :aria-hidden="isMobileViewport && !mobileNavOpen"
      :inert="isMobileViewport && !mobileNavOpen"
    >
      <div class="brand">
        <div class="brand-icon" aria-hidden="true">
          <svg viewBox="0 0 48 48" fill="none">
            <circle cx="13" cy="33" r="3.2" fill="currentColor" />
            <circle cx="24" cy="25" r="3.2" fill="currentColor" />
            <path d="M13 33c3.3-6.8 7.2-7.2 11-8 5-1.2 6.3-7.2 12-11" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" />
            <path d="m31.5 12.5 5 1-1 5" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" />
            <path d="M12 39h25" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity=".35" />
          </svg>
        </div>
        <div class="brand-copy">
          <strong>求职助手</strong>
          <span>让机会更近一步</span>
        </div>
      </div>

      <nav class="nav-list" aria-label="应用主导航">
        <button
          v-for="item in visibleAppNavItems"
          :key="item.label"
          type="button"
          class="nav-item"
          :class="{ active: isAppNavActive(item), disabled: !item.enabled }"
          :disabled="!item.enabled"
          @click="item.enabled && item.path && navigateTo(item.path)"
        >
          <span class="nav-icon">{{ item.icon }}</span>
          <span class="nav-copy">
            <strong>{{ item.label }}</strong>
          </span>
        </button>
      </nav>

      <div class="sidebar-card">
        <p>周五 08:00 生产内容</p>
        <p>周五 09:00 创建公众号草稿</p>
        <p>审核动作统一进入任务链</p>
      </div>
    </aside>

    <main
      ref="mainViewport"
      class="main"
      :class="{
        'skills-route': currentRoute === '/skills',
        'career-route': currentRoute === '/career',
        'interview-library-route': currentRoute.startsWith('/interviews'),
        'prompt-preview-route': currentRoute === '/review/prompts',
        'review-home-route': currentRoute === '/review'
      }"
    >
      <header class="topbar">
        <button
          type="button"
          class="mobile-nav-toggle"
          :aria-expanded="mobileNavOpen"
          aria-controls="app-navigation"
          aria-label="打开导航"
          @click="toggleMobileNavigation"
        >
          <span aria-hidden="true"></span>
          <span aria-hidden="true"></span>
          <span aria-hidden="true"></span>
        </button>
        <h1>{{ activeRoute.label }}</h1>
        <div ref="accountMenuRef" class="topbar-account">
          <button
            type="button"
            class="account-breadcrumb"
            :class="{ active: accountMenuOpen }"
            aria-haspopup="menu"
            :aria-expanded="accountMenuOpen"
            aria-controls="account-menu"
            @click="toggleAccountMenu"
          >
            <span class="account-breadcrumb-avatar" aria-hidden="true">{{ accountInitial }}</span>
            <span class="account-breadcrumb-name">{{ accountDisplayName }}</span>
            <i aria-hidden="true">/</i>
            <span class="account-breadcrumb-role">{{ accountRoleLabel }}</span>
            <span class="account-breadcrumb-chevron" aria-hidden="true"></span>
          </button>

          <div v-if="accountMenuOpen" id="account-menu" class="account-menu" role="menu" aria-label="账户菜单">
            <button
              type="button"
              class="account-menu-option"
              role="menuitem"
              :aria-expanded="accountMenuSection === 'profile'"
              @click="toggleAccountMenuSection('profile')"
            >
              <span class="account-menu-option-icon" aria-hidden="true">人</span>
              <span><strong>个人信息</strong><small>查看账户与权限</small></span>
              <i aria-hidden="true"></i>
            </button>
            <div v-if="accountMenuSection === 'profile'" class="account-menu-panel account-profile-panel">
              <div><span>名称</span><strong>{{ accountDisplayName }}</strong></div>
              <div class="account-identifier-row"><span>账号</span><strong :title="accountIdentifier">{{ accountIdentifier }}</strong></div>
              <div><span>角色</span><strong>{{ accountRoleLabel }}</strong></div>
              <button type="button" class="account-logout" @click="logoutPlatform">退出登录</button>
            </div>

            <button
              type="button"
              class="account-menu-option"
              role="menuitem"
              :aria-expanded="accountMenuSection === 'theme'"
              @click="toggleAccountMenuSection('theme')"
            >
              <span class="account-menu-option-icon theme-icon" aria-hidden="true"></span>
              <span><strong>主题切换</strong><small>{{ uiTheme === 'blue' ? '当前为蓝白主题' : '当前为浅绿主题' }}</small></span>
              <i aria-hidden="true"></i>
            </button>
            <div v-if="accountMenuSection === 'theme'" class="account-menu-panel account-theme-panel">
              <ThemeSwitcher :model-value="uiTheme" @update:model-value="handleAccountThemeChange" />
            </div>
          </div>
        </div>
      </header>

      <section v-if="errorMessage" class="alert danger">
        {{ errorMessage }}
      </section>
      <section v-if="actionMessage" class="alert success">
        {{ actionMessage }}
      </section>

      <section v-if="!canAccessCurrentRoute" class="route-access-state">
        <span aria-hidden="true">—</span>
        <h2>当前没有可访问的模块</h2>
        <p>管理员尚未为此账户开放页面，请联系管理员调整路由模块配置。</p>
        <button v-if="firstAccessibleRoute" type="button" class="secondary-button" @click="navigateTo(firstAccessibleRoute)">前往可用模块</button>
      </section>

      <CareerAssistantPage
        v-else-if="currentRoute === '/career'"
        :interview-master-visible="careerInterviewMasterVisible"
        :online-assessment-visible="careerOnlineAssessmentVisible"
        @navigate="navigateTo"
      />

      <InterviewLibraryPage v-else-if="currentRoute === '/interviews'" />

      <JobSearchWorkspace v-else-if="currentRoute === '/interviews/jobs'" />

      <ObservabilityPage v-else-if="currentRoute === '/observability'" />

      <AdminConsolePage
        v-else-if="currentRoute.startsWith('/admin/')"
        :current-route="currentRoute"
        @navigate="navigateTo"
        @navigation-config-updated="applyNavigationConfig"
      />

      <template v-else-if="currentRoute === '/skills'">
        <section class="skills-workspace">
          <aside class="skill-sub-sidebar">
            <div class="skill-sidebar-title">
              <div>
                <p class="eyebrow">Skills</p>
                <h2>已安装技能</h2>
              </div>
              <strong>{{ skills.length }}</strong>
            </div>

            <input
              v-model="installedSkillQuery"
              class="installed-skill-search"
              type="text"
              placeholder="搜索已安装 Skill 名称或描述"
              aria-label="搜索已安装 Skill"
            />

            <div v-if="skillsLoading" class="skill-empty">正在读取本地 Skill...</div>
            <div v-else-if="!skills.length" class="skill-empty">暂未扫描到 Skill。</div>
            <div v-else-if="!filteredInstalledSkills.length" class="skill-empty">没有匹配的已安装 Skill。</div>
            <div v-else class="installed-skill-list">
              <article
                v-for="skill in filteredInstalledSkills"
                :key="skill.id"
                class="installed-skill-entry"
              >
                <button
                  type="button"
                  class="installed-skill-item"
                  :class="{ active: selectedSkillId === skill.id }"
                  @click="loadSkillDetail(skill.id)"
                >
                  <span class="installed-skill-topline">
                    <strong>{{ skill.name }}</strong>
                  </span>
                  <span>{{ skillDescription(skill) }}</span>
                  <small>{{ skill.source_label }} · {{ skill.repository_full_name || skill.path_hint }}</small>
                  <small v-if="skill.repository_full_name" class="skill-star-line">{{ formatStars(skill.stars) }}</small>
                  <small v-else class="skill-local-line">本地 Skill</small>
                </button>

                <button
                  v-if="skill.repository_full_name"
                  type="button"
                  class="star-stat-trigger"
                  :class="{ empty: typeof skill.stars !== 'number' || weeklyStarDelta(skill) === null }"
                  :style="starRingStyle(skill)"
                  :aria-label="`查看 ${skill.name} 的 Star 统计`"
                  @mouseenter="openStarPopover(skill, $event)"
                  @mouseleave="scheduleStarPopoverClose"
                  @focus="openStarPopover(skill, $event)"
                  @blur="scheduleStarPopoverClose"
                  @click.stop="toggleStarPopover(skill, $event)"
                >
                  <i class="star-ring" aria-hidden="true"></i>
                </button>
              </article>
            </div>
          </aside>

          <section class="skills-main-panel">
            <section class="skill-search-panel">
              <div class="skill-page-heading">
                <div>
                  <h2>搜索 GitHub 开放 Skill</h2>
                  <p>输入关键词，搜索开放 Skill，并在下方查看对应的 SKILL.md。</p>
                </div>
              </div>

              <form class="skill-search-row" @submit.prevent="searchSkills(false)">
                <input
                  v-model="skillSearchQuery"
                  type="text"
                  placeholder="例如：technical blog writing、image prompt、video storyboard、wechat layout"
                />
                <button type="submit" class="refresh-button" :disabled="skillSearchLoading">
                  {{ skillSearchLoading ? '搜索中...' : '搜索 Skill' }}
                </button>
                <button
                  type="button"
                  class="secondary-button"
                  :disabled="skillSearchLoading || !skillSearchQuery.trim()"
                  @click="searchSkills(true)"
                >
                  刷新 GitHub
                </button>
              </form>

              <div class="skill-info-strip">
                <span>GitHub 搜索 SKILL.md</span>
                <span>find-skills 质量策略</span>
                <template v-if="skillSearchSnapshot">
                  <span>{{ skillSnapshotSourceLabel(skillSearchSnapshot.dataSource) }}</span>
                  <span>{{ skillSnapshotStateLabel(skillSearchSnapshot.cacheState) }}</span>
                  <span>{{ skillSnapshotTime(skillSearchSnapshot.snapshotAt) }}</span>
                </template>
              </div>

              <section v-if="skillErrorMessage" class="alert danger">
                {{ skillErrorMessage }}
              </section>
              <section v-if="skillMessage" class="alert success">
                {{ skillMessage }}
              </section>

              <div v-if="skillSearchResults.length" class="skill-result-list">
                <button
                  v-for="result in skillSearchResults"
                  :key="result.skill.id"
                  type="button"
                  class="skill-result-item"
                  :class="{ active: selectedSkillId === result.skill.id }"
                  @click="selectSearchResult(result)"
                >
                  <span>
                    <strong>{{ result.skill.name }}</strong>
                    <small>{{ skillDescription(result.skill) }}</small>
                    <small>作者：{{ result.skill.author || result.skill.source_label }} · 仓库：{{ result.skill.repository_full_name || '未知' }}</small>
                    <small>{{ result.match_reason }}</small>
                  </span>
                  <span class="result-score-stack">
                    <em>{{ result.score }}%</em>
                    <small>{{ formatStars(result.skill.stars) }}</small>
                  </span>
                </button>
              </div>
            </section>

            <section class="skill-editor-panel">
              <div class="panel-header">
                <div>
                  <p class="eyebrow">SKILL.md</p>
                  <h2>{{ selectedSkill?.name ?? '请选择一个 Skill' }}</h2>
                  <p v-if="selectedSkill">
                    {{ skillDescription(selectedSkill) }}
                  </p>
                </div>
                <span class="status-pill" :class="selectedSkill?.editable ? 'is-success' : 'is-muted'">
                  {{ selectedSkill?.editable ? '项目本地可更新' : '保存为项目本地副本' }}
                </span>
              </div>

              <div v-if="selectedSkill" class="skill-detail-meta">
                <span>作者 / 来源：{{ selectedSkill.author || selectedSkill.source_label }}</span>
                <span>Star：{{ formatStars(selectedSkill.stars) }}</span>
                <span>本周增量：{{ formatWeeklyStarDelta(selectedSkill) }}</span>
                <span>位置：{{ selectedSkill.path_hint }}</span>
                <a
                  v-if="isExternalSkillLink(selectedSkill)"
                  :href="selectedSkill.homepage_url"
                  target="_blank"
                  rel="noreferrer"
                >
                  外部链接：{{ selectedSkill.homepage_url }}
                </a>
                <span v-else>外部链接：暂无</span>
              </div>

              <div v-if="!selectedSkill" class="skill-empty editor-empty">
                从左侧列表或搜索结果中选择一个 Skill 后，这里会展示 SKILL.md。
              </div>
              <template v-else>
                <textarea
                  v-model="skillEditorMarkdown"
                  class="skill-markdown-editor"
                  spellcheck="false"
                  aria-label="编辑 SKILL.md"
                ></textarea>

                <div class="skill-save-row">
                  <label>
                    保存名称
                    <input
                      v-model="skillSaveName"
                      type="text"
                      placeholder="例如：technical-blog-writing"
                    />
                  </label>
                  <button type="button" class="action-button approve" :disabled="skillSaving" @click="saveSkill">
                    {{ skillSaving ? '保存中...' : '保存并更新' }}
                  </button>
                </div>
              </template>
            </section>
          </section>
        </section>
      </template>

      <template v-else-if="currentRoute === '/review'">
        <section class="review-home-scroll">
        <section class="health-card">
          <div>
            <h2>全局健康总览</h2>
            <p>{{ healthSummary?.message ?? '最近任务、历史异常和缺失资源会汇总到这里。' }}</p>
          </div>
          <div class="health-stats">
            <span><strong>{{ healthSummary?.blocking_item_count ?? 0 }}</strong><small>当前阻塞</small></span>
            <span><strong>{{ healthSummary?.failed_run_count ?? 0 }}</strong><small>当前失败</small></span>
            <span><strong>{{ healthSummary?.historical_failed_run_count ?? 0 }}</strong><small>历史失败</small></span>
          </div>
          <div v-if="blockingReasons.length" class="reason-row">
            <span v-for="reason in blockingReasons" :key="reason.reason" class="reason-chip">
              {{ reason.reason }} × {{ reason.count }}
            </span>
          </div>
        </section>

        <ManualPipelinePanel v-if="authUser?.role === 'admin'" />

        <section class="module-grid">
          <article
            v-for="card in moduleCards"
            :key="card.path"
            class="module-card"
            :class="`accent-${card.accent}`"
            role="button"
            tabindex="0"
            @click="navigateTo(card.path)"
            @keydown.enter.prevent="navigateTo(card.path)"
          >
            <h2>{{ card.title }}</h2>
            <p>{{ shortText(card.description, 150) }}</p>
            <div class="mini-preview" :class="`preview-${card.kind}`">
              <template v-if="card.path === '/review/article'">
                <div class="article-mini-copy">
                  <span class="article-mini-kicker">本周技术周报</span>
                  <strong>{{ shortText(content?.title, 42) }}</strong>
                  <p>{{ articleExcerpt }}</p>
                </div>
                <div class="blue-pill">本周主线</div>
                <span></span><span></span><span></span>
              </template>
              <template v-else-if="card.path === '/review/pipeline'">
                <div class="mini-task-list">
                  <div v-for="task in taskCards" :key="`all-${task.taskName}`" class="mini-task">
                    <strong>{{ task.taskName }}</strong>
                    <em :class="statusClass(task.status)">{{ statusText(task.status) }}</em>
                  </div>
                </div>
                <div v-for="task in taskCards.slice(0, 3)" :key="task.taskName" class="mini-task">
                  <strong>{{ task.taskName }}</strong>
                  <em :class="statusClass(task.status)">{{ statusText(task.status) }}</em>
                </div>
              </template>
              <template v-else-if="card.path === '/review/assets'">
                <div class="mini-assets">
                  <figure v-for="asset in overviewImageAssets" :key="`thumbnail-${asset.id}`" class="mini-asset-thumbnail">
                    <img v-if="asset.preview_url" :src="asset.preview_url" :alt="`项目插图 ${asset.id}`" loading="lazy" />
                    <span v-else>图片</span>
                  </figure>
                </div>
                <small class="mini-asset-summary">图片 {{ imageAssets.length }} · 音频 {{ audioAssets.length }} · 视频 {{ videoAssets.length }}</small>
                <div v-for="asset in mediaAssets.slice(0, 6)" :key="asset.id" class="mini-asset">
                  {{ mediaTypeLabel(asset.asset_type).slice(0, 1) }}
                </div>
              </template>
              <template v-else-if="card.path === '/review/history'">
                <div class="mini-history-track" aria-hidden="true">
                  <span class="history-node article-node">推文</span>
                  <i></i>
                  <span class="history-node asset-node">素材</span>
                </div>
                <div class="mini-history-copy">
                  <strong>每次执行独立归档</strong>
                  <small>以 content_id 为边界查看推文与媒体文件</small>
                </div>
              </template>
              <template v-else>
                <div class="mini-prompt-list">
                  <div v-for="(prompt, index) in imagePrompts.slice(0, 3)" :key="`explain-${prompt.repository_full_name}`" class="mini-prompt-explained">
                    <strong>图 {{ index + 1 }} · {{ shortText(prompt.repository_full_name, 24) }}</strong>
                    <small>{{ promptPreviewText(prompt) }}</small>
                  </div>
                </div>
                <div v-for="prompt in imagePrompts.slice(0, 3)" :key="prompt.repository_full_name" class="mini-prompt">
                  {{ shortText(prompt.repository_full_name, 26) }}
                </div>
              </template>
            </div>
            <button type="button">打开 {{ card.title }}</button>
          </article>
        </section>

        <section v-if="isStoryboardReviewVisible" class="storyboard-home" role="button" tabindex="0" @click="navigateTo('/review/storyboard')" @keydown.enter.prevent="navigateTo('/review/storyboard')">
          <div>
            <h2>短视频蓝图</h2>
            <p>查看 7 段分镜、递进口播和视频提示词</p>
          </div>
          <div class="timeline-preview">
            <span>0-5s</span>
            <span>项目1</span>
            <span>项目2</span>
            <span>项目3</span>
            <span>项目4</span>
            <span>项目5</span>
            <span>CTA</span>
          </div>
        </section>
        </section>
      </template>

      <template v-else-if="currentRoute === '/review/article'">
        <section class="detail-toolbar">
          <button
            type="button"
            @click="navigateTo(contentIdFromLocation() ? `/review/assets?content_id=${activeContentId}` : '/review')"
          >
            {{ contentIdFromLocation() ? '← 返回本次归档' : '← 返回总览' }}
          </button>
          <span>{{ contentIdFromLocation() ? `历史原文 · content_id=${activeContentId}` : '只读预览：正文内容不在审核台直接修改' }}</span>
        </section>

        <article class="detail-card article-detail" :class="{ 'is-archive-article': contentIdFromLocation() }">
          <header class="wechat-article-header">
            <span>{{ content?.week_end ?? '技术周报' }}</span>
            <h2>{{ wechatTitle }}</h2>
            <small>
              {{ contentIdFromLocation()
                ? `${formatArchiveTimestamp(content?.created_at)} · 本页只读展示该次生成时保存的原文`
                : '审核预览与微信公众号草稿使用同一篇排版正文' }}
            </small>
          </header>
          <div class="panel-header">
            <div>
              <p class="eyebrow">Article Preview</p>
              <h2>{{ content?.title ?? '暂无内容' }}</h2>
            </div>
            <span class="week-tag">{{ content?.week_end ?? '等待生成' }}</span>
          </div>
          <p class="digest">{{ content?.digest ?? 'SummaryTask 完成后会在这里展示摘要。' }}</p>
          <div
            v-if="articleLayout?.article_html"
            v-openable-images
            class="article-body rendered"
            v-html="articleLayout.article_html"
            @click="handleArticleImagePreview"
          ></div>
          <div v-else class="article-body">
            <pre>{{ previewText(content?.article_markdown, '暂无正文') }}</pre>
          </div>
        </article>

        <section v-if="!contentIdFromLocation()" class="review-action-row">
          <div class="review-state">
            <p class="eyebrow">Approval</p>
            <h2>人工审核</h2>
            <span class="status-pill" :class="approvalClass(approval?.decision)">
              {{ approvalText(approval?.decision) }}
            </span>
            <p v-if="approval">
              {{ approval.operator || '未署名' }} · {{ approval.created_at }}
              <br />
              {{ approval.comment || '无备注' }}
            </p>
            <p v-else>通过后会立即进入排版与微信公众号草稿创建链路；驳回或请求重生成会触发 SummaryTask。</p>
          </div>

          <div class="review-form">
            <label for="operatorName">审核人</label>
            <input id="operatorName" v-model="operatorName" type="text" placeholder="例如：content-reviewer" />
            <label for="approvalComment">审核备注</label>
            <textarea
              id="approvalComment"
              v-model="approvalComment"
              rows="3"
              placeholder="可选：写下修改意见，驳回/重生成时会追加进生成 prompt"
            ></textarea>
          </div>

          <div class="review-buttons">
            <button
              type="button"
              class="action-button approve"
              :disabled="Boolean(actionLoading) || !content?.id"
              @click="submitReviewAction('approved')"
            >
              {{ actionLoading === 'approved' ? '推送中...' : '通过并推送草稿箱' }}
            </button>
            <button
              type="button"
              class="action-button reject"
              :disabled="Boolean(actionLoading) || !content?.id"
              @click="submitReviewAction('rejected')"
            >
              {{ actionLoading === 'rejected' ? '重生成中...' : '驳回并重生成' }}
            </button>
            <button
              type="button"
              class="action-button regenerate"
              :disabled="Boolean(actionLoading) || !content?.id"
              @click="submitReviewAction('regenerate_requested')"
            >
              {{ actionLoading === 'regenerate_requested' ? '请求中...' : '请求重生成' }}
            </button>
          </div>
        </section>
      </template>

      <template v-else-if="currentRoute === '/review/pipeline'">
        <section class="detail-toolbar">
          <button type="button" @click="navigateTo('/review')">← 返回总览</button>
          <span>任务运行状态只读预览</span>
        </section>
        <section class="detail-card pipeline-detail-card">
          <div class="panel-header">
            <div>
              <h2>主任务生命周期</h2>
              <p>从内容生成到人工审核，集中查看本次生产进度。</p>
            </div>
            <span class="status-pill" :class="healthClass(healthSummary?.health_status)">
              {{ healthText(healthSummary?.health_status ?? 'unknown') }}
            </span>
          </div>

          <section class="lifecycle-panel" aria-labelledby="lifecycle-heading">
            <div class="lifecycle-heading">
              <div>
                <h3 id="lifecycle-heading">生产进度</h3>
                <p>五个阶段全部完成后，内容才进入可交付状态。</p>
              </div>
              <span>{{ completedLifecycleStageCount }}/{{ lifecycleStages.length }} 已完成</span>
            </div>
            <ol class="lifecycle-flow">
              <li
                v-for="(stage, index) in lifecycleStages"
                :key="stage.key"
                class="lifecycle-stage"
                :class="{ ready: stage.ready }"
              >
                <div class="lifecycle-marker" aria-hidden="true">{{ index + 1 }}</div>
                <div class="lifecycle-copy">
                  <span>{{ stage.label }}</span>
                  <strong>{{ stage.status }}</strong>
                  <small>{{ stage.detail }}</small>
                </div>
              </li>
            </ol>
          </section>

          <div class="task-detail-heading">
            <div>
              <h3>执行任务明细</h3>
              <p>用于定位具体 Task 的运行记录与失败原因。</p>
            </div>
            <span>{{ taskCards.filter((task) => task.status === 'succeeded').length }}/{{ taskCards.length }} 个任务成功</span>
          </div>
          <div class="task-list expanded">
            <article v-for="task in taskCards" :key="task.taskName" class="task-card">
              <div>
                <strong>{{ task.taskName }}</strong>
                <small>{{ task.runId || '暂无运行记录' }}</small>
                <p v-if="taskNote(task)" :class="{ 'task-error': task.errorMessage }">{{ taskNote(task) }}</p>
              </div>
              <span class="status-pill" :class="statusClass(task.status)">
                {{ statusText(task.status) }}
              </span>
            </article>
          </div>
        </section>
      </template>

      <template v-else-if="currentRoute === '/review/assets'">
        <section class="detail-toolbar">
          <button type="button" @click="navigateTo(contentIdFromLocation() ? '/review/history' : '/review')">
            {{ contentIdFromLocation() ? '← 返回执行历史' : '← 返回总览' }}
          </button>
          <button type="button" class="secondary-button" :disabled="upgradingImages || !content?.id" @click="upgradeGithubImages">
            {{ upgradingImages ? '抓取中...' : '尝试复用 GitHub 项目图' }}
          </button>
        </section>
        <p v-if="imageUpgradeMessage" class="inline-message">{{ imageUpgradeMessage }}</p>
        <section class="detail-card">
          <div class="panel-header">
            <div>
              <p class="eyebrow">Assets</p>
              <h2>媒体素材</h2>
              <p v-if="activeContentId" class="asset-scope-note">
                content_id={{ activeContentId }} · {{ shortText(content?.title, 72) }}
              </p>
            </div>
            <span class="asset-count">
              媒体文件 {{ mediaLibrarySummary.total_asset_count ?? 0 }} ·
              图片 {{ mediaLibrarySummary.image_count ?? 0 }} ·
              音频 {{ mediaLibrarySummary.audio_count ?? 0 }} ·
              视频 {{ mediaLibrarySummary.video_count ?? 0 }}
            </span>
          </div>
          <div v-if="!mediaLibraryAssets.length" class="empty-media">
            目前还没有可预览的实际媒体文件。下方会单独显示已经规划、正在生成或失败的视频片段。
          </div>
          <div v-else class="media-grid">
            <article v-for="asset in mediaLibraryAssets" :key="asset.id" class="media-card">
              <div class="media-preview">
                <button
                  v-if="asset.asset_type === 'image' && asset.preview_url"
                  type="button"
                  class="media-image-link"
                  :aria-label="`预览图片 ${asset.id}`"
                  title="点击预览图片"
                  @click="openImagePreview(asset.preview_url, `图片 ${asset.id}`)"
                >
                  <img :src="asset.preview_url" :alt="`图片 ${asset.id}`" />
                </button>
                <audio v-else-if="asset.asset_type === 'audio' && asset.preview_url" controls :src="asset.preview_url"></audio>
                <video v-else-if="isPlayableVideoAsset(asset)" controls :src="asset.preview_url"></video>
                <span v-else>{{ mediaTypeLabel(asset.asset_type) }}</span>
              </div>
              <div class="media-meta">
                <strong>{{ mediaTypeLabel(asset.asset_type) }} #{{ asset.id }}</strong>
                <small>{{ asset.provider }} · {{ mediaStatusText(asset.status) }}</small>
                <small v-if="asset.metadata?.repository_full_name">{{ asset.metadata.repository_full_name }}</small>
                <small v-if="asset.status === 'failed' && mediaFailureReason(asset)" class="media-failure">
                  失败原因：{{ mediaFailureReason(asset) }}
                </small>
                <button
                  v-if="asset.asset_type === 'image' && asset.preview_url"
                  type="button"
                  class="media-preview-action"
                  @click="openImagePreview(asset.preview_url, `图片 ${asset.id}`)"
                >
                  打开预览
                </button>
                <a v-else-if="asset.preview_url" :href="asset.preview_url" target="_blank" rel="noreferrer">打开预览</a>
              </div>
            </article>
          </div>

          <section v-if="pendingVideoClips.length" class="pending-video-section">
            <div class="panel-header compact-panel-header">
              <div>
                <p class="eyebrow">Video generation</p>
                <h3>尚未产出文件的视频片段</h3>
              </div>
              <span class="asset-count">{{ pendingVideoClips.length }} 段</span>
            </div>
            <div class="pending-video-list">
              <article v-for="clip in pendingVideoClips" :key="`pending-video-${clip.id}`" class="pending-video-card">
                <div>
                  <strong>片段 {{ clip.clip_index }} · {{ clip.clip_title }}</strong>
                  <small>{{ clip.provider }} · 计划 {{ clip.planned_duration_seconds }} 秒</small>
                </div>
                <div class="pending-video-status">
                  <span class="status-pill" :class="statusClass(clip.status)">{{ mediaStatusText(clip.status) }}</span>
                  <small v-if="clip.status === 'planned'">尚未提交视频生成，因此当前没有可预览文件。</small>
                  <small v-else-if="clip.status === 'failed'">生成失败；请在任务流程中查看失败原因后重试。</small>
                  <small v-else>正在等待视频文件写入资源库。</small>
                </div>
              </article>
            </div>
          </section>
        </section>

        <button
          v-if="activeContentId"
          type="button"
          class="archive-article-entry"
          @click="navigateTo(`/review/article?content_id=${activeContentId}`)"
        >
          <span class="archive-article-entry-label">历史原文</span>
          <div class="archive-article-entry-heading">
            <div>
              <h2>文章原文</h2>
              <p>{{ shortText(content?.title, 72) }}</p>
            </div>
            <span>content_id={{ activeContentId }}</span>
          </div>
          <div class="archive-article-entry-preview" aria-hidden="true">
            <span>本周技术周报</span>
            <strong>{{ shortText(wechatTitle, 54) }}</strong>
            <p>{{ articleExcerpt }}</p>
            <i></i><i></i><i></i>
          </div>
          <div class="archive-article-entry-footer">
            <time :datetime="content?.created_at">{{ formatArchiveTimestamp(content?.created_at) }}</time>
            <strong>打开完整原文 →</strong>
          </div>
        </button>
      </template>

      <template v-else-if="currentRoute === '/review/history'">
        <section class="detail-toolbar">
          <button type="button" @click="navigateTo('/review')">← 返回总览</button>
          <button type="button" class="secondary-button" :disabled="executionHistoryLoading" @click="loadExecutionHistory">
            {{ executionHistoryLoading ? '刷新中...' : '刷新执行历史' }}
          </button>
        </section>

        <section class="detail-card execution-history-card">
          <div class="panel-header execution-history-header">
            <div>
              <p class="eyebrow">Execution Archive</p>
              <h2>执行历史</h2>
              <p>每个 content_id 只保留对应推文与本次生成的专属媒体素材。</p>
            </div>
            <span class="asset-count">{{ executionHistory.length }} 条归档</span>
          </div>

          <p v-if="executionHistoryError" class="history-error">{{ executionHistoryError }}</p>
          <div v-else-if="executionHistoryLoading" class="empty-media">正在读取执行历史…</div>
          <div v-else-if="!executionHistory.length" class="empty-media">
            还没有可查看的执行归档。完成一次内容生成后，这里会按 content_id 显示推文与素材数量。
          </div>
          <div v-else class="execution-history-list">
            <button
              v-for="item in executionHistory"
              :key="item.content_id"
              type="button"
              class="execution-history-item"
              @click="navigateTo(`/review/assets?content_id=${item.content_id}`)"
            >
              <div class="history-item-heading">
                <span class="history-content-id">content_id={{ item.content_id }}</span>
                <span class="status-pill" :class="statusClass(item.status)">{{ statusText(item.status) }}</span>
              </div>
              <h3>{{ item.title || `第 ${item.content_id} 次内容生成` }}</h3>
              <p>{{ shortText(item.digest, 150) || '该次推文尚未生成摘要。' }}</p>
              <div class="history-item-footer">
                <div class="history-asset-summary">
                  <strong>{{ item.assets?.total_asset_count ?? 0 }} 个有效素材</strong>
                  <span>
                    图片 {{ item.assets?.image_count ?? 0 }} ·
                    音频 {{ item.assets?.audio_count ?? 0 }} ·
                    视频 {{ item.assets?.video_count ?? 0 }}
                  </span>
                </div>
                <div class="history-item-bottom">
                  <time class="history-item-time" :datetime="item.created_at">
                    {{ formatArchiveTimestamp(item.created_at) }}
                  </time>
                  <em>查看原文与媒体资源 →</em>
                </div>
              </div>
            </button>
          </div>
        </section>
      </template>

      <template v-else-if="currentRoute === '/review/storyboard'">
        <section class="detail-toolbar">
          <button type="button" @click="navigateTo('/review')">← 返回总览</button>
          <span>短视频蓝图只读预览</span>
        </section>
        <section class="detail-card">
          <div class="panel-header">
            <div>
              <p class="eyebrow">Short Video Blueprint</p>
              <h2>短视频蓝图</h2>
            </div>
            <span class="asset-count">{{ videoStoryboard ? '已生成' : '未生成' }}</span>
          </div>
          <div v-if="!videoStoryboard" class="empty-media">
            ShortVideoPromptTask 完成后，这里会展示渐进式口播、7 段分镜和 Seedance 主提示词。
          </div>
          <div v-else class="storyboard-grid">
            <article>
              <p class="eyebrow">Progressive Script</p>
              <pre>{{ videoStoryboard.progressive_script }}</pre>
            </article>
            <article>
              <p class="eyebrow">Seedance Prompt</p>
              <pre>{{ videoStoryboard.seedance_prompt }}</pre>
            </article>
          </div>
          <div v-if="videoStoryboard?.storyboard?.scenes?.length" class="scene-list">
            <article v-for="scene in videoStoryboard.storyboard.scenes" :key="scene.scene_index" class="scene-card">
              <strong>{{ scene.time_range }} · {{ scene.purpose }}</strong>
              <small v-if="scene.repository_full_name">{{ scene.repository_full_name }}</small>
              <p>{{ scene.narration }}</p>
              <p>画面：{{ scene.visual_design }}</p>
              <p>运动：{{ scene.motion_design }}</p>
            </article>
          </div>
        </section>
      </template>

      <template v-else-if="currentRoute === '/review/prompts'">
        <section class="detail-toolbar">
          <button type="button" @click="navigateTo('/review')">← 返回总览</button>
        </section>
        <section class="prompt-grid">
          <article class="detail-card prompt-column">
            <header class="prompt-column-header"><h2>图片总结方案</h2></header>
            <div class="prompt-column-scroll">
              <div v-if="!imagePrompts.length" class="empty-media">暂无图片总结方案。</div>
              <div v-else class="prompt-list">
                <article
                  v-for="(prompt, index) in imagePrompts"
                  :key="prompt.repository_full_name || index"
                  :class="{ 'visual-plan-card': prompt.prompt_stage === 'deterministic_rendered' }"
                >
                  <strong>图 {{ index + 1 }} · {{ prompt.repository_full_name }}</strong>
                  <template v-if="prompt.prompt_stage === 'deterministic_rendered'">
                    <div class="visual-plan-evidence">
                      <span>{{ figureRoleLabel(prompt.figure_role || prompt.visual_spec?.figure_role) }}</span>
                      <span
                        class="status-pill"
                        :class="visualValidationClass(prompt.validation_result)"
                      >
                        {{ visualValidationLabel(prompt.validation_result) }}
                      </span>
                      <small>模板 {{ prompt.template_version || '未知' }}</small>
                      <small>标识 {{ shortRenderKey(prompt.render_key) }}</small>
                    </div>
                    <div
                      class="visual-plan-copy"
                      :aria-label="visualSpecSummary(prompt.visual_spec)"
                    >
                      <h3>{{ prompt.visual_spec?.headline || '未提供标题' }}</h3>
                      <p>{{ prompt.visual_spec?.purpose || '未提供总结目的' }}</p>
                      <ul v-if="visualTakeaways(prompt).length" class="visual-plan-takeaways">
                        <li
                          v-for="takeaway in visualTakeaways(prompt)"
                          :key="takeaway"
                        >
                          {{ takeaway }}
                        </li>
                      </ul>
                    </div>
                  </template>
                  <template v-else>
                    <p>{{ prompt.summary_text }}</p>
                    <pre>{{ prompt.prompt }}</pre>
                  </template>
                </article>
              </div>
            </div>
          </article>
          <article class="detail-card prompt-column">
            <header class="prompt-column-header"><h2>视频提示词</h2></header>
            <div class="prompt-column-scroll">
              <pre>{{ videoStoryboard?.seedance_prompt || '暂无 Seedance 主提示词' }}</pre>
              <div v-if="videoClipPlans.length" class="prompt-list compact">
                <article v-for="clip in videoClipPlans" :key="clip.id">
                  <strong>{{ clip.clip_title }}</strong>
                  <p>{{ clip.output_start_second }}s-{{ clip.output_end_second }}s · {{ clip.repository_full_name || '全局片段' }}</p>
                  <pre>{{ clip.seedance_prompt }}</pre>
                </article>
              </div>
            </div>
          </article>
        </section>
      </template>
    </main>

    <ImagePreviewDialog
      v-if="imagePreviewDialog"
      :source="imagePreviewDialog.source"
      :alt="imagePreviewDialog.alt"
      @close="closeImagePreview"
    />

    <Teleport to="body">
      <aside
        v-if="starPopoverSkill"
        class="skill-star-popover"
        :style="starPopoverPosition"
        role="tooltip"
        @mouseenter="keepStarPopoverOpen"
        @mouseleave="scheduleStarPopoverClose"
      >
        <div class="skill-star-popover-heading">
          <div>
            <p>STAR 动态</p>
            <strong>{{ starPopoverSkill.name }}</strong>
          </div>
          <span>{{ starPopoverSkill.repository_full_name || '未关联 GitHub 仓库' }}</span>
        </div>

        <div class="skill-star-popover-content">
          <div
            class="skill-star-popover-ring"
            :class="{ empty: typeof starPopoverSkill.stars !== 'number' || weeklyStarDelta(starPopoverSkill) === null }"
            :style="starRingStyle(starPopoverSkill)"
          >
            <strong>{{ starRingLabel(starPopoverSkill) }}</strong>
            <small>新增占比</small>
          </div>

          <div class="skill-star-metrics">
            <div>
              <small>总 Star 数</small>
              <strong>{{ formatStars(starPopoverSkill.stars) }}</strong>
            </div>
            <div>
              <small>本周新增 Star</small>
              <strong :class="{ negative: weeklyStarDelta(starPopoverSkill) !== null && weeklyStarDelta(starPopoverSkill) < 0 }">
                {{ formatWeeklyStarDelta(starPopoverSkill) }}
              </strong>
            </div>
          </div>
        </div>

        <p class="skill-star-popover-note">{{ starGrowthText(starPopoverSkill) }}</p>
      </aside>
    </Teleport>
  </div>
</template>

<style scoped>
.visual-plan-evidence {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin: 10px 0 14px;
  padding: 10px 0;
  border-block: 1px solid var(--ui-line);
  color: var(--ui-text-muted);
}

.visual-plan-evidence > span:first-child {
  color: var(--ui-accent-ink);
  font-weight: 800;
}

.visual-plan-evidence small {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}

.visual-plan-copy h3 {
  margin: 0 0 8px;
  color: var(--ui-text);
  font-size: 18px;
  line-height: 1.4;
}

.visual-plan-copy > p {
  margin: 0;
}

.visual-plan-takeaways {
  display: grid;
  gap: 6px;
  margin: 12px 0 0;
  padding-left: 20px;
  color: var(--ui-text);
  line-height: 1.6;
}
</style>
