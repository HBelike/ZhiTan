<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { JobLibraryBridgeError, jobLibraryBridge } from '../job-library-bridge.js'
import {
  normalizeBossExtensionConnection,
  shouldOpenBossExtensionGate
} from '../boss-extension-onboarding.js'
import {
  greetingJobKey,
  normalizeGreetingLimit,
  toggleGreetingJob
} from '../career-greeting-preview.js'
import {
  beginSelectionIntent,
  cancelSelectionIntent,
  createSelectionIntentState,
  isCurrentSelectionIntent,
  resetSelectionIntents
} from '../job-search-multi-selection.js'
import {
  createJobDetailLoader,
  prefetchJobDetails
} from '../job-detail-prefetch.js'
import {
  DEFAULT_JOB_CITY,
  mergeJobCityCatalog,
  normalizeHotJobCities
} from '../job-city-catalog.js'
import JobCityPicker from './JobCityPicker.vue'
import BossExtensionInstallDialog from './BossExtensionInstallDialog.vue'

const CITY_STORAGE_KEY = 'find-job-job-city-v1'

function readStoredCity() {
  try {
    const value = JSON.parse(window.localStorage.getItem(CITY_STORAGE_KEY))
    if (/^\d{9}$/.test(String(value?.code ?? '')) && String(value?.name ?? '').trim()) {
      return value
    }
  } catch {
    // 浏览器禁用本地存储时继续使用默认城市。
  }
  return { ...DEFAULT_JOB_CITY }
}

const props = defineProps({
  selectionMode: { type: Boolean, default: false },
  multiSelectionMode: { type: Boolean, default: false },
  selectionLimit: { type: Number, default: 10 }
})

const emit = defineEmits(['selection-change', 'selection-list-change'])

const query = ref('')
const submittedQuery = ref('')
const jobs = ref([])
const selectedJobId = ref('')
const selectedJob = ref(null)
const compactView = ref('list')
const page = ref(1)
const hasMore = ref(false)
const hasSearched = ref(false)
const searching = ref(false)
const loadingMore = ref(false)
const detailLoading = ref(false)
const searchError = ref('')
const detailError = ref('')
const selectedJobs = ref([])
const multiSelectionError = ref('')
const selectionLoadingKeys = ref([])
const selectionErrors = ref({})
const bridgeStatus = ref('checking')
const bridgeVersion = ref('')
const installDialogOpen = ref(false)
const searchInput = ref(null)
const selectedCity = ref(readStoredCity())
const submittedCity = ref({ ...selectedCity.value })
const cityCatalog = ref(mergeJobCityCatalog())
const hotCities = ref(normalizeHotJobCities())
const cityLoading = ref(false)
let searchSequence = 0
let detailSequence = 0
let prefetchSequence = 0
let selectionClosed = false
const selectionIntents = createSelectionIntentState()
const detailLoader = createJobDetailLoader({
  keyOf: greetingJobKey,
  fetchDetail: (job) => jobLibraryBridge.getJobDetail(job)
})

const bridgeStatusText = computed(() => ({
  checking: '正在连接助手',
  ready: bridgeVersion.value ? `助手已连接 · v${bridgeVersion.value}` : '助手已连接',
  missing: '需要安装助手',
  attention: '需要处理 BOSS 页面'
}[bridgeStatus.value]))

const selectedSkills = computed(() => selectedJob.value?.skills ?? [])
const selectedWelfare = computed(() => selectedJob.value?.welfare ?? [])
const resultSummary = computed(() => jobs.value.length
  ? `已加载 ${jobs.value.length} 个岗位`
  : '等待真实结果')
const resultCityName = computed(() => (hasSearched.value ? submittedCity.value : selectedCity.value).name)
const detailFetchedAt = computed(() => {
  const value = selectedJob.value?.fetchedAt
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(date)
})
const normalizedSelectionLimit = computed(() => normalizeGreetingLimit(props.selectionLimit))
const selectedJobCount = computed(() => selectedJobs.value.length)

function readableBridgeError(error, fallback) {
  if (error instanceof JobLibraryBridgeError) return error.message
  return error instanceof Error ? error.message : fallback
}

function markBridgeAttention(error) {
  if (!(error instanceof JobLibraryBridgeError)) return
  if (error.code === 'extension_unavailable') bridgeStatus.value = 'missing'
  if ([
    'login_required',
    'verification_required',
    'rate_limited',
    'city_mismatch',
    'city_context_sync_failed',
    'session_refresh_failed'
  ].includes(error.code)) {
    bridgeStatus.value = 'attention'
  }
}

function publishSelection(job) {
  if (props.selectionMode) emit('selection-change', job)
}

function publishSelectionList() {
  if (props.multiSelectionMode) emit('selection-list-change', [...selectedJobs.value])
}

function isJobInSelection(job) {
  const key = greetingJobKey(job)
  return selectedJobs.value.some((item) => greetingJobKey(item) === key)
}

function removeSelectedJob(job) {
  const result = toggleGreetingJob(
    selectedJobs.value,
    job,
    normalizedSelectionLimit.value
  )
  selectedJobs.value = result.jobs
  multiSelectionError.value = result.error
  publishSelectionList()
}

function isSelectionLoading(job) {
  return selectionLoadingKeys.value.includes(greetingJobKey(job))
}

function selectionError(job) {
  return selectionErrors.value[greetingJobKey(job)] || ''
}

function setSelectionLoading(key, loading) {
  selectionLoadingKeys.value = loading
    ? [...new Set([...selectionLoadingKeys.value, key])]
    : selectionLoadingKeys.value.filter((item) => item !== key)
}

function clearSelectionError(key) {
  const next = { ...selectionErrors.value }
  delete next[key]
  selectionErrors.value = next
}

function mergeJobDetail(detail) {
  jobs.value = jobs.value.map((item) => (
    greetingJobKey(item) === greetingJobKey(detail) ? { ...item, ...detail } : item
  ))
  if (selectedJob.value && greetingJobKey(selectedJob.value) === greetingJobKey(detail)) {
    selectedJob.value = { ...selectedJob.value, ...detail }
  }
  let selectionChanged = false
  selectedJobs.value = selectedJobs.value.map((item) => {
    if (greetingJobKey(item) !== greetingJobKey(detail)) return item
    selectionChanged = true
    return { ...item, ...detail }
  })
  if (selectionChanged) publishSelectionList()
}

function shouldStopDetailPrefetch(error) {
  return error instanceof JobLibraryBridgeError && [
    'login_required',
    'verification_required',
    'rate_limited',
    'session_refresh_failed'
  ].includes(error.code)
}

function prefetchCurrentJobs(jobList, searchToken) {
  const sequence = ++prefetchSequence
  const selectedKeys = new Set(selectedJobs.value.map(greetingJobKey))
  const prioritized = [...jobList].sort((left, right) => (
    Number(selectedKeys.has(greetingJobKey(right))) - Number(selectedKeys.has(greetingJobKey(left)))
  ))
  void prefetchJobDetails(prioritized, detailLoader, {
    concurrency: 2,
    onResolved: (detail) => {
      if (selectionClosed || sequence !== prefetchSequence || searchToken !== searchSequence) return
      mergeJobDetail(detail)
      clearSelectionError(greetingJobKey(detail))
    },
    onRejected: (error, job) => {
      if (selectionClosed || sequence !== prefetchSequence || searchToken !== searchSequence) return
      markBridgeAttention(error)
      if (isJobInSelection(job)) {
        selectionErrors.value = {
          ...selectionErrors.value,
          [greetingJobKey(job)]: readableBridgeError(error, '岗位详情预取失败，点击卡片可重试。')
        }
      }
    },
    shouldStop: shouldStopDetailPrefetch
  })
}

async function hydrateSelectedJob(job, token) {
  const key = greetingJobKey(job)
  setSelectionLoading(key, true)
  try {
    const detail = await detailLoader.load(job)
    if (selectionClosed || !isCurrentSelectionIntent(selectionIntents, key, token)) return
    mergeJobDetail(detail)
    clearSelectionError(key)
  } catch (error) {
    if (selectionClosed || !isCurrentSelectionIntent(selectionIntents, key, token)) return
    markBridgeAttention(error)
    selectionErrors.value = {
      ...selectionErrors.value,
      [key]: readableBridgeError(error, '岗位详情获取失败，点击卡片可重试。')
    }
  } finally {
    if (isCurrentSelectionIntent(selectionIntents, key, token)) {
      cancelSelectionIntent(selectionIntents, key)
      setSelectionLoading(key, false)
    }
  }
}

async function toggleJobFromCard(job) {
  if (!props.multiSelectionMode) return
  const key = greetingJobKey(job)
  multiSelectionError.value = ''
  clearSelectionError(key)

  if (isJobInSelection(job)) {
    cancelSelectionIntent(selectionIntents, key)
    setSelectionLoading(key, false)
    removeSelectedJob(job)
    return
  }
  if (isSelectionLoading(job)) {
    cancelSelectionIntent(selectionIntents, key)
    setSelectionLoading(key, false)
    return
  }
  if (selectedJobs.value.length >= normalizedSelectionLimit.value) {
    multiSelectionError.value = `本批最多选择 ${normalizedSelectionLimit.value} 个岗位。`
    return
  }

  const token = beginSelectionIntent(selectionIntents, key)
  const cached = detailLoader.peek(job)
  const result = toggleGreetingJob(
    selectedJobs.value,
    cached ?? job,
    normalizedSelectionLimit.value
  )
  selectedJobs.value = result.jobs
  multiSelectionError.value = result.error
  if (result.error) {
    cancelSelectionIntent(selectionIntents, key)
    return
  }
  publishSelectionList()
  if (cached) {
    mergeJobDetail(cached)
    cancelSelectionIntent(selectionIntents, key)
    return
  }
  void hydrateSelectedJob(job, token)
}

function resetJobResults() {
  searchSequence += 1
  detailSequence += 1
  prefetchSequence += 1
  detailLoader.clear()
  jobs.value = []
  selectedJobId.value = ''
  selectedJob.value = null
  compactView.value = 'list'
  page.value = 1
  hasMore.value = false
  hasSearched.value = false
  submittedQuery.value = ''
  searchError.value = ''
  detailError.value = ''
  searching.value = false
  loadingMore.value = false
  detailLoading.value = false
  resetSelectionIntents(selectionIntents)
  selectionLoadingKeys.value = []
  selectionErrors.value = {}
  publishSelection(null)
}

function handleCityChange(city) {
  if (!city?.code || city.code === selectedCity.value.code) return
  selectedCity.value = { ...city }
  try {
    window.localStorage.setItem(CITY_STORAGE_KEY, JSON.stringify(selectedCity.value))
  } catch {
    // 本地存储不可用不影响本次搜索。
  }
  if (hasSearched.value || jobs.value.length) resetJobResults()
}

async function loadCities() {
  if (cityLoading.value) return
  cityLoading.value = true
  try {
    const result = await jobLibraryBridge.listCities()
    cityCatalog.value = mergeJobCityCatalog(result)
    hotCities.value = normalizeHotJobCities(result)
    const refreshedCity = cityCatalog.value.find((city) => city.code === selectedCity.value.code)
    if (refreshedCity) selectedCity.value = refreshedCity
  } catch {
    // 城市目录请求失败时使用内置热门城市，岗位搜索仍可继续。
  } finally {
    cityLoading.value = false
  }
}

async function checkBridge({ loadCatalog = true, interactive = false } = {}) {
  bridgeStatus.value = 'checking'
  try {
    const connection = normalizeBossExtensionConnection(await jobLibraryBridge.ping())
    bridgeStatus.value = connection.status
    bridgeVersion.value = connection.version
    installDialogOpen.value = shouldOpenBossExtensionGate(connection.status, { interactive })
    searchError.value = connection.status === 'ready' ? '' : '未检测到职位库浏览器助手。'
    if (connection.status === 'ready' && loadCatalog) void loadCities()
    return connection.status === 'ready'
  } catch (error) {
    bridgeStatus.value = 'missing'
    bridgeVersion.value = ''
    installDialogOpen.value = shouldOpenBossExtensionGate('missing', { interactive })
    searchError.value = readableBridgeError(error, '未检测到职位库浏览器助手。')
    return false
  }
}

function handleConnectionBadge() {
  if (bridgeStatus.value === 'missing') {
    installDialogOpen.value = true
    return
  }
  void checkBridge()
}

function refreshForExtensionCheck() {
  window.location.reload()
}

async function submitSearch() {
  const nextQuery = query.value.trim()
  if (!nextQuery || searching.value) return
  if (!await checkBridge({ loadCatalog: false, interactive: true })) return
  const sequence = ++searchSequence
  prefetchSequence += 1
  detailLoader.clear()
  searching.value = true
  searchError.value = ''
  detailError.value = ''
  publishSelection(null)
  submittedQuery.value = nextQuery
  submittedCity.value = { ...selectedCity.value }
  hasSearched.value = true

  try {
    const result = await jobLibraryBridge.searchJobs(nextQuery, 1, submittedCity.value)
    if (sequence !== searchSequence) return
    jobs.value = result.jobs ?? []
    page.value = 1
    hasMore.value = Boolean(result.hasMore)
    selectedJobId.value = ''
    selectedJob.value = null
    if (jobs.value.length) {
      void selectJob(jobs.value[0], { showDetail: false })
      prefetchCurrentJobs(jobs.value, sequence)
    }
  } catch (error) {
    if (sequence !== searchSequence) return
    jobs.value = []
    hasMore.value = false
    selectedJobId.value = ''
    selectedJob.value = null
    markBridgeAttention(error)
    searchError.value = readableBridgeError(error, '真实岗位获取失败，请稍后重试。')
  } finally {
    if (sequence === searchSequence) searching.value = false
  }
}

async function selectJob(job, { showDetail = true } = {}) {
  const sequence = ++detailSequence
  if (showDetail) compactView.value = 'detail'
  publishSelection(null)
  selectedJobId.value = job.id
  const cached = detailLoader.peek(job)
  selectedJob.value = cached ?? job
  if (cached) {
    mergeJobDetail(cached)
    bridgeStatus.value = 'ready'
    publishSelection(cached)
    detailLoading.value = false
    detailError.value = ''
    return
  }
  detailLoading.value = true
  detailError.value = ''
  try {
    const detail = await detailLoader.load(job)
    if (sequence !== detailSequence) return
    selectedJob.value = detail
    mergeJobDetail(detail)
    bridgeStatus.value = 'ready'
    publishSelection(detail)
  } catch (error) {
    if (sequence !== detailSequence) return
    markBridgeAttention(error)
    detailError.value = readableBridgeError(error, '岗位详情获取失败，请点击当前卡片重试。')
    publishSelection(null)
  } finally {
    if (sequence === detailSequence) detailLoading.value = false
  }
}

function returnToJobList() {
  compactView.value = 'list'
}

async function loadMore() {
  if (!hasMore.value || loadingMore.value || !submittedQuery.value) return
  loadingMore.value = true
  searchError.value = ''
  try {
    const nextPage = page.value + 1
    const result = await jobLibraryBridge.searchJobs(submittedQuery.value, nextPage, submittedCity.value)
    const knownIds = new Set(jobs.value.map((job) => job.id))
    const additions = (result.jobs ?? []).filter((job) => !knownIds.has(job.id))
    jobs.value = [...jobs.value, ...additions]
    page.value = nextPage
    hasMore.value = Boolean(result.hasMore) && additions.length > 0
    bridgeStatus.value = 'ready'
    if (additions.length) prefetchCurrentJobs(additions, searchSequence)
  } catch (error) {
    markBridgeAttention(error)
    searchError.value = readableBridgeError(error, '更多岗位获取失败，请稍后重试。')
  } finally {
    loadingMore.value = false
  }
}

onMounted(() => {
  void checkBridge()
  nextTick(() => searchInput.value?.focus())
})

onBeforeUnmount(() => {
  selectionClosed = true
  resetSelectionIntents(selectionIntents)
})
</script>

<template>
  <section class="job-workspace">
    <header class="job-workspace-header">
      <div class="workspace-intro">
        <h2>查找真实岗位</h2>
        <p>选择工作城市并输入岗位名称，职位库会通过已登录浏览器读取当前在招信息。</p>
      </div>
      <div class="workspace-header-actions">
        <span v-if="multiSelectionMode" class="selection-badge">
          已选 <strong>{{ selectedJobCount }}</strong>/{{ normalizedSelectionLimit }}
        </span>
        <button
          type="button"
          class="connection-badge"
          :class="`is-${bridgeStatus}`"
          :disabled="bridgeStatus === 'checking'"
          @click="handleConnectionBadge"
        >
          <i aria-hidden="true"></i>
          {{ bridgeStatusText }}
        </button>
      </div>
    </header>

    <form class="search-rail" :class="{ searching }" @submit.prevent="submitSearch">
      <label for="job-search-input">岗位名称</label>
      <div class="search-input-shell">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="6.5"></circle>
          <path d="m16 16 4 4"></path>
        </svg>
        <input
          id="job-search-input"
          ref="searchInput"
          v-model="query"
          type="search"
          maxlength="80"
          autocomplete="off"
          placeholder="例如：Agent 软件开发"
        />
      </div>
      <JobCityPicker
        :model-value="selectedCity"
        :cities="cityCatalog"
        :hot-cities="hotCities"
        :loading="cityLoading"
        @update:model-value="handleCityChange"
      />
      <button type="submit" :disabled="searching || !query.trim()">
        <span v-if="searching" class="button-loader" aria-hidden="true"></span>
        {{ searching ? '正在检索…' : '搜索真实岗位' }}
      </button>
      <div class="live-track" aria-hidden="true"><i></i></div>
    </form>

    <section v-if="searchError" class="job-alert" :class="{ 'is-setup': bridgeStatus === 'missing' }">
      <div>
        <strong>{{ bridgeStatus === 'missing' ? '连接职位库助手后即可搜索' : '本次没有获取到岗位' }}</strong>
        <p>{{ searchError }}</p>
        <p v-if="bridgeStatus === 'missing'" class="setup-path">
          当前浏览器尚未连接助手。下载 ZIP 后可按照 Chrome 或 Edge 图文教程完成安装。
        </p>
      </div>
      <button type="button" @click="installDialogOpen = true">查看安装说明</button>
    </section>

    <p v-if="multiSelectionError" class="multi-selection-error" role="alert">
      {{ multiSelectionError }}
    </p>

    <div
      class="job-browser"
      :class="{ 'is-searching': searching, 'show-compact-detail': compactView === 'detail' }"
    >
      <aside class="job-results" aria-label="岗位搜索结果">
        <header class="result-heading">
          <div>
            <strong>{{ submittedQuery || '真实岗位结果' }}</strong>
            <span>{{ resultCityName }} · 当前会话返回</span>
          </div>
          <span class="result-count">{{ resultSummary }}</span>
        </header>

        <div v-if="!hasSearched" class="result-empty">
          <span aria-hidden="true">⌕</span>
          <strong>选择城市并输入岗位名称开始</strong>
          <p>不需要粘贴 URL，也不会跳转离开职位库。</p>
        </div>
        <div v-else-if="!searching && !jobs.length && !searchError" class="result-empty">
          <span aria-hidden="true">—</span>
          <strong>没有找到匹配岗位</strong>
          <p>尝试缩短岗位名称，例如只搜索“Agent”或“Java”。</p>
        </div>

        <div v-if="jobs.length" class="job-card-list">
          <article
            v-for="job in jobs"
            :key="job.id"
            class="job-card"
            :class="{ active: selectedJobId === job.id, selected: isJobInSelection(job), 'has-selection': multiSelectionMode }"
            role="button"
            tabindex="0"
            @click="selectJob(job)"
            @keydown.enter.prevent="selectJob(job)"
            @keydown.space.prevent="selectJob(job)"
          >
            <span class="card-track-node" aria-hidden="true"></span>
            <button
              v-if="multiSelectionMode"
              type="button"
              class="job-selection-checkbox"
              :class="{ checked: isJobInSelection(job), loading: isSelectionLoading(job) }"
              role="checkbox"
              :aria-checked="isJobInSelection(job)"
              :aria-label="`${isJobInSelection(job) ? '移除' : '选择'}岗位：${job.title}`"
              @click.stop="toggleJobFromCard(job)"
            >
              <span aria-hidden="true">{{ isJobInSelection(job) ? '✓' : '' }}</span>
              <i v-if="isSelectionLoading(job)" class="selection-detail-loader" aria-hidden="true"></i>
            </button>
            <span class="job-card-topline">
              <strong>{{ job.title }}</strong>
              <em>{{ job.salary }}</em>
            </span>
            <span class="job-tags">
              <i>{{ job.experience }}</i>
              <i>{{ job.degree }}</i>
              <i v-if="job.skills[0]">{{ job.skills[0] }}</i>
            </span>
            <span class="company-row">
              <span class="company-mark" aria-hidden="true">{{ job.companyShort.slice(0, 1) }}</span>
              <span class="company-name">{{ job.companyShort }}</span>
              <span class="company-location">{{ job.district || job.city }}</span>
            </span>
            <span v-if="selectionError(job)" class="job-selection-error" role="alert">
              {{ selectionError(job) }}
            </span>
          </article>
        </div>

        <button v-if="jobs.length && hasMore" type="button" class="load-more" :disabled="loadingMore" @click="loadMore">
          <span v-if="loadingMore" class="button-loader" aria-hidden="true"></span>
          {{ loadingMore ? '正在加载…' : '加载更多岗位' }}
          <span v-if="!loadingMore" aria-hidden="true">↓</span>
        </button>
        <p v-else-if="jobs.length" class="result-end">已展示当前搜索返回的全部岗位</p>
      </aside>

      <article v-if="selectedJob" class="job-detail" :class="{ refreshing: detailLoading }" aria-live="polite">
        <button
          type="button"
          class="detail-back-button"
          aria-label="返回岗位列表"
          @click="returnToJobList"
        >返回岗位列表</button>
        <div v-if="detailLoading" class="detail-refreshing">
          <span class="button-loader" aria-hidden="true"></span>
          正在获取最新详情
        </div>

        <header class="detail-hero">
          <div class="detail-title-block">
            <span class="detail-track-node" aria-hidden="true"></span>
            <div class="title-line">
              <h3>{{ selectedJob.title }}</h3>
              <strong>{{ selectedJob.salary }}</strong>
            </div>
            <div class="detail-meta">
              <span>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s6-5.2 6-11a6 6 0 1 0-12 0c0 5.8 6 11 6 11Z"></path><circle cx="12" cy="10" r="2"></circle></svg>
                {{ selectedJob.city || submittedCity.name }}
              </span>
              <span>{{ selectedJob.experience }}</span>
              <span>{{ selectedJob.degree }}</span>
            </div>
          </div>
          <div class="detail-hero-actions">
            <div class="freshness">
              <span><i aria-hidden="true"></i>详情已更新</span>
              <small>{{ detailFetchedAt || '本次会话' }}</small>
            </div>
          </div>
        </header>

        <section class="company-strip">
          <span class="company-logo" aria-hidden="true">{{ selectedJob.companyShort.slice(0, 1) }}</span>
          <div>
            <strong>{{ selectedJob.company }}</strong>
            <p>{{ [selectedJob.industry, selectedJob.stage, selectedJob.scale].filter(Boolean).join(' · ') || '公司信息以 BOSS 当前页面为准' }}</p>
          </div>
          <span class="recruiter-online" :class="{ offline: !selectedJob.recruiterOnline }">
            <i aria-hidden="true"></i>{{ selectedJob.recruiter || '招聘者信息待补充' }}
          </span>
        </section>

        <div class="detail-scroll">
          <section v-if="detailError" class="detail-error">
            <strong>详情暂时没有刷新成功</strong>
            <p>{{ detailError }}</p>
            <button type="button" @click="selectJob(selectedJob)">重新获取详情</button>
          </section>

          <section v-if="selectedSkills.length" class="job-summary">
            <div class="skill-list">
              <span v-for="skill in selectedSkills" :key="skill">{{ skill }}</span>
            </div>
          </section>

          <section class="description-section">
            <h4>职位描述</h4>
            <div v-if="selectedJob.description" class="job-description">{{ selectedJob.description }}</div>
            <p v-else class="description-empty">职位详情暂未提供正文，可再次点击左侧卡片刷新。</p>
          </section>

          <section v-if="selectedJob.address" class="description-section">
            <h4>工作地点</h4>
            <p>{{ selectedJob.address }}</p>
          </section>

          <section v-if="selectedWelfare.length" class="description-section welfare-section">
            <h4>职位福利</h4>
            <div class="welfare-list">
              <span v-for="item in selectedWelfare" :key="item">{{ item }}</span>
            </div>
          </section>

          <a v-if="selectedJob.sourceUrl" class="source-link" :href="selectedJob.sourceUrl" target="_blank" rel="noreferrer">
            在 BOSS 直聘查看原岗位
            <span aria-hidden="true">↗</span>
          </a>
        </div>
      </article>

      <section v-else class="detail-empty">
        <span aria-hidden="true">◎</span>
        <strong>{{ searching ? '正在查找真实岗位' : '选择一个岗位查看详情' }}</strong>
        <p>{{ searching ? '职位列表返回后会自动读取第一条岗位详情。' : '每次点击岗位卡片，都会重新获取最新职位描述。' }}</p>
      </section>
    </div>
  </section>

  <BossExtensionInstallDialog
    :open="installDialogOpen"
    :error="bridgeStatus === 'ready' ? '' : searchError"
    mode="install"
    @close="installDialogOpen = false"
    @refresh="refreshForExtensionCheck"
  />
</template>

<style scoped>
.job-workspace {
  --job-canvas: var(--ui-canvas, #f3f7fc);
  --job-paper: var(--ui-surface, #fff);
  --job-ink: var(--ui-text, #14213d);
  --job-copy: var(--ui-text-secondary, #526078);
  --job-muted: var(--ui-text-muted, #7b879c);
  --job-line: var(--ui-line, #d7e4f6);
  --job-line-strong: var(--ui-line-strong, #b9cfed);
  --job-blue: var(--ui-accent, #0869d8);
  --job-blue-ink: var(--ui-accent-ink, #004aa8);
  --job-blue-soft: var(--ui-surface-active, #eaf3ff);
  --job-teal: #04a6a6;
  --job-red: #f05b52;
  display: flex;
  min-height: 0;
  min-width: 0;
  container: job-search / inline-size;
  flex: 1;
  flex-direction: column;
  gap: 12px;
  color: var(--job-ink);
  font-family: var(--ui-font-body, Inter, "Segoe UI Variable Text", "PingFang SC", sans-serif);
}

.job-workspace-header { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 18px; }
.workspace-intro h2 { margin: 0; color: var(--job-ink); font-family: var(--ui-font-display, "Segoe UI Variable Display", "PingFang SC", sans-serif); font-size: 20px; font-weight: 760; letter-spacing: -.025em; }
.workspace-intro p { margin: 3px 0 0; color: var(--job-muted); font-size: 12px; }
.workspace-header-actions { display: flex; align-items: center; gap: 9px; }
.selection-badge { border: 1px solid var(--job-line); border-radius: 999px; background: var(--job-blue-soft); color: var(--job-blue-ink); padding: 6px 10px; font: 750 10px/1.2 var(--ui-font-utility, Consolas, monospace); }
.selection-badge strong { font-size: 12px; }
.connection-badge { display: inline-flex; align-items: center; gap: 7px; border: 1px solid color-mix(in srgb, var(--job-teal) 28%, transparent); border-radius: 999px; background: color-mix(in srgb, var(--job-teal) 8%, var(--job-paper)); color: #087d7d; padding: 6px 10px; cursor: pointer; font-family: var(--ui-font-utility, Consolas, monospace); font-size: 10px; font-weight: 700; }
.connection-badge i,.recruiter-online i,.freshness i { width: 7px; height: 7px; flex: none; border-radius: 50%; background: var(--job-teal); box-shadow: 0 0 0 3px color-mix(in srgb, var(--job-teal) 14%, transparent); }
.connection-badge.is-checking i { animation: status-pulse .8s ease-in-out infinite alternate; }
.connection-badge.is-missing,.connection-badge.is-attention { border-color: color-mix(in srgb, #d99a35 36%, transparent); background: #fff8e8; color: #8c621c; }
.connection-badge.is-missing i,.connection-badge.is-attention i { background: #d99a35; box-shadow: 0 0 0 3px rgba(217,154,53,.14); }

.search-rail { position: relative; z-index: 10; display: grid; grid-template-columns: auto minmax(320px, 720px) auto auto 1fr; align-items: center; gap: 12px; min-height: 66px; overflow: visible; border: 1px solid var(--job-line); border-radius: 14px; background: var(--job-paper); padding: 12px 16px 14px; }
.search-rail > label { color: var(--job-copy); font-size: 12px; font-weight: 800; }
.search-input-shell { display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 10px; min-height: 42px; border: 1px solid var(--job-line-strong); border-radius: 10px; background: color-mix(in srgb, var(--job-paper) 84%, var(--job-blue-soft)); padding: 0 11px; transition: border-color .16s ease, box-shadow .16s ease; }
.search-input-shell:focus-within { border-color: var(--job-blue); box-shadow: 0 0 0 3px var(--ui-focus, rgba(8, 105, 216, .13)); }
.search-input-shell svg { width: 18px; fill: none; stroke: var(--job-blue); stroke-linecap: round; stroke-width: 1.8; }
.search-input-shell input { min-width: 0; border: 0; outline: 0; background: transparent; color: var(--job-ink); font-size: 15px; font-weight: 650; }
.search-input-shell input::placeholder { color: var(--job-muted); font-weight: 500; }
.search-rail > button { display: inline-flex; min-width: 136px; min-height: 42px; align-items: center; justify-content: center; gap: 8px; border: 0; border-radius: 10px; background: var(--job-blue); color: #fff; padding: 0 18px; cursor: pointer; font-size: 13px; font-weight: 820; box-shadow: 0 7px 18px color-mix(in srgb, var(--job-blue) 18%, transparent); }
.search-rail > button:hover:not(:disabled) { background: var(--ui-accent-hover, #0058c7); }
.search-rail > button:disabled { cursor: wait; opacity: .68; }
.button-loader { width: 13px; height: 13px; flex: none; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: job-spin .7s linear infinite; }
.live-track { position: absolute; right: 0; bottom: 0; left: 0; height: 3px; background: var(--job-blue-soft); }
.live-track i { display: block; width: 32%; height: 100%; background: linear-gradient(90deg, var(--job-blue), var(--job-teal)); transition: width .35s ease; }
.search-rail.searching .live-track i { width: 100%; animation: search-pulse 1.1s ease-in-out infinite alternate; }

.job-alert { display: flex; align-items: center; justify-content: space-between; gap: 20px; border: 1px solid #efd8b1; border-radius: 11px; background: #fff9ed; color: #795721; padding: 11px 14px; }
.job-alert > div { min-width: 0; }
.job-alert strong { font-size: 12px; }
.job-alert p { margin: 3px 0 0; font-size: 11px; line-height: 1.55; }
.job-alert .setup-path { color: #8b6b35; }
.job-alert code { font-family: var(--ui-font-utility, Consolas, monospace); font-size: 10px; }
.multi-selection-error { margin: -4px 0 0; border-left: 3px solid #c94955; border-radius: 0 8px 8px 0; background: #fff3f4; color: #a33b46; padding: 7px 10px; font-size: 11px; font-weight: 750; }
.job-alert button,.detail-error button { flex: none; border: 1px solid #ddb96f; border-radius: 7px; background: #fff; color: #805d22; padding: 7px 10px; cursor: pointer; font-size: 11px; font-weight: 750; }

.job-browser { position: relative; z-index: 1; display: grid; min-height: 0; flex: 1; grid-template-columns: 370px minmax(0, 1fr); overflow: hidden; border: 1px solid var(--job-line); border-radius: 14px; background: var(--job-paper); }
.job-results { min-width: 0; overflow-y: auto; border-right: 1px solid var(--job-line); background: color-mix(in srgb, var(--job-canvas) 65%, var(--job-paper)); }
.result-heading { position: sticky; z-index: 3; top: 0; display: flex; align-items: center; justify-content: space-between; gap: 14px; border-bottom: 1px solid var(--job-line); background: color-mix(in srgb, var(--job-paper) 94%, transparent); backdrop-filter: blur(12px); padding: 14px 15px 12px 20px; }
.result-heading > div { display: grid; min-width: 0; gap: 3px; }
.result-heading strong { overflow: hidden; color: var(--job-ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.result-heading span { color: var(--job-muted); font-size: 10px; }
.result-count { flex: none; border-radius: 999px; background: var(--job-blue-soft); color: var(--job-blue-ink) !important; padding: 5px 8px; font-family: var(--ui-font-utility, Consolas, monospace); }
.result-empty,.detail-empty { display: grid; place-items: center; align-content: center; color: var(--job-muted); padding: 28px; text-align: center; }
.result-empty { min-height: 260px; }
.result-empty > span,.detail-empty > span { display: grid; width: 42px; height: 42px; place-items: center; border-radius: 13px; background: var(--job-blue-soft); color: var(--job-blue); font-size: 22px; }
.result-empty strong,.detail-empty strong { margin-top: 13px; color: var(--job-ink); font-size: 14px; }
.result-empty p,.detail-empty p { max-width: 330px; margin: 7px 0 0; font-size: 11px; line-height: 1.65; }
.detail-empty { min-width: 0; background: var(--job-paper); }
.job-card-list { display: grid; gap: 9px; padding: 11px; }
.job-card { position: relative; display: grid; gap: 12px; width: 100%; overflow: hidden; border: 1px solid transparent; border-radius: 12px; background: var(--job-paper); color: var(--job-ink); padding: 15px 14px 13px 17px; cursor: pointer; text-align: left; transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease; }
.job-card:hover { border-color: var(--job-line-strong); transform: translateY(-1px); }
.job-card.active { border-color: var(--job-blue); box-shadow: 0 8px 22px color-mix(in srgb, var(--job-blue) 9%, transparent); }
.job-card.selected { border-color: color-mix(in srgb, var(--job-teal) 55%, var(--job-line)); background: color-mix(in srgb, var(--job-teal) 5%, var(--job-paper)); }
.job-card.has-selection { padding-left: 45px; }
.job-card:focus-visible { outline: 0; border-color: var(--job-blue); box-shadow: 0 0 0 3px var(--ui-focus, rgba(8,105,216,.13)); }
.card-track-node { position: absolute; top: 17px; bottom: 17px; left: 0; width: 3px; border-radius: 0 3px 3px 0; background: transparent; }
.job-card.active .card-track-node { background: linear-gradient(var(--job-blue), var(--job-teal)); }
.job-card.selected .card-track-node { background: var(--job-teal); }
.job-selection-checkbox { position: absolute; z-index: 2; top: 14px; left: 13px; display: grid; width: 22px; height: 22px; place-items: center; border: 1px solid var(--job-line-strong); border-radius: 7px; background: var(--job-paper); color: #fff; padding: 0; cursor: pointer; }
.job-selection-checkbox:hover,.job-selection-checkbox:focus-visible { border-color: var(--job-blue); outline: 0; box-shadow: 0 0 0 3px var(--ui-focus, rgba(8,105,216,.13)); }
.job-selection-checkbox.checked { border-color: var(--job-teal); background: var(--job-teal); font-size: 13px; font-weight: 900; }
.job-selection-checkbox.loading { cursor: progress; }
.selection-detail-loader { position: absolute; right: -4px; bottom: -4px; width: 8px; height: 8px; border: 2px solid var(--job-paper); border-top-color: var(--job-blue); border-radius: 50%; background: var(--job-paper); animation: jobSpin .8s linear infinite; }
.job-selection-error { border-radius: 7px; background: #fff3f4; color: #a33b46; padding: 6px 8px; font-size: 10px; line-height: 1.45; }
.job-card-topline { display: flex; min-width: 0; align-items: start; justify-content: space-between; gap: 12px; }
.job-card.selected .job-card-topline { padding-right: 38px; }
.job-card-topline strong { min-width: 0; overflow: hidden; font-size: 15px; font-weight: 750; letter-spacing: -.015em; text-overflow: ellipsis; white-space: nowrap; }
.job-card-topline em { flex: none; color: var(--job-red); font-size: 15px; font-style: normal; font-weight: 800; }
.job-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.job-tags i { border-radius: 5px; background: color-mix(in srgb, var(--job-canvas) 76%, var(--job-paper)); color: var(--job-copy); padding: 4px 7px; font-size: 10px; font-style: normal; }
.company-row { display: grid; grid-template-columns: 23px minmax(0, 1fr) auto; align-items: center; gap: 8px; padding-top: 1px; }
.company-mark,.company-logo { display: grid; place-items: center; border-radius: 7px; background: linear-gradient(145deg, var(--job-blue-soft), color-mix(in srgb, var(--job-teal) 12%, #fff)); color: var(--job-blue-ink); font-weight: 850; }
.company-mark { width: 23px; height: 23px; font-size: 10px; }
.company-name { min-width: 0; overflow: hidden; color: var(--job-copy); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.company-location { color: var(--job-muted); font-size: 10px; }
.load-more { display: flex; width: calc(100% - 22px); min-height: 38px; align-items: center; justify-content: center; gap: 7px; margin: 1px 11px 14px; border: 1px solid var(--job-line); border-radius: 9px; background: var(--job-paper); color: var(--job-blue-ink); cursor: pointer; font-size: 11px; font-weight: 750; }
.load-more:hover:not(:disabled) { border-color: var(--job-blue); background: var(--job-blue-soft); }
.load-more:disabled { opacity: .7; }
.result-end { margin: 4px 0 18px; color: var(--job-muted); font-size: 10px; text-align: center; }

.job-detail { position: relative; min-width: 0; overflow: hidden; background: var(--job-paper); transition: opacity .18s ease; }
.detail-back-button { display:none; min-height:40px; border:1px solid var(--job-line); border-radius:9px; background:var(--job-paper); color:var(--job-blue-ink); padding:7px 11px; font-size:12px; font-weight:800; }
.detail-hero-actions { display: flex; flex: none; align-items: center; gap: 12px; }
.detail-refreshing { position: absolute; z-index: 5; top: 14px; right: 20px; display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--job-line); border-radius: 999px; background: var(--job-paper); color: var(--job-blue-ink); padding: 6px 10px; font-size: 10px; font-weight: 750; box-shadow: 0 8px 22px rgba(20, 33, 61, .08); }
.detail-hero { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 24px; border-bottom: 1px solid var(--job-line); padding: 22px 28px 18px; }
.detail-title-block { position: relative; min-width: 0; }
.detail-track-node { position: absolute; top: 2px; bottom: 2px; left: -28px; width: 3px; border-radius: 0 3px 3px 0; background: linear-gradient(var(--job-blue), var(--job-teal)); }
.title-line { display: flex; min-width: 0; align-items: baseline; gap: 20px; }
.title-line h3 { min-width: 0; margin: 0; color: var(--job-ink); font-family: var(--ui-font-display, "Segoe UI Variable Display", sans-serif); font-size: clamp(22px, 2vw, 29px); font-weight: 770; letter-spacing: -.035em; }
.title-line > strong { flex: none; color: var(--job-red); font-size: 24px; letter-spacing: -.02em; }
.detail-meta { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 11px; color: var(--job-copy); font-size: 12px; }
.detail-meta span { display: inline-flex; align-items: center; gap: 5px; }
.detail-meta svg { width: 15px; fill: none; stroke: var(--job-muted); stroke-width: 1.7; }
.freshness { display: flex; flex-direction: column; align-items: end; justify-content: center; gap: 5px; color: var(--job-copy); }
.freshness > span { display: inline-flex; align-items: center; gap: 7px; font-size: 11px; font-weight: 750; }
.freshness small { color: var(--job-muted); font-family: var(--ui-font-utility, Consolas, monospace); font-size: 9px; }
.company-strip { display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; align-items: center; gap: 12px; border-bottom: 1px solid var(--job-line); background: color-mix(in srgb, var(--job-canvas) 45%, var(--job-paper)); padding: 13px 28px; }
.company-logo { width: 42px; height: 42px; font-size: 16px; }
.company-strip > div { min-width: 0; }
.company-strip strong { display: block; overflow: hidden; color: var(--job-ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.company-strip p { margin: 4px 0 0; color: var(--job-muted); font-size: 10px; }
.recruiter-online { display: inline-flex; align-items: center; gap: 8px; color: var(--job-copy); font-size: 10px; }
.recruiter-online.offline i { background: #a8b1be; box-shadow: 0 0 0 3px rgba(168,177,190,.14); }
.detail-scroll { height: calc(100% - 150px); overflow-y: auto; padding: 20px 28px 40px; }
.detail-error { display: flex; align-items: center; justify-content: space-between; gap: 14px; border: 1px solid #efd8b1; border-radius: 10px; background: #fff9ed; padding: 12px 14px; }
.detail-error strong { color: #795721; font-size: 12px; }
.detail-error p { flex: 1; margin: 0; color: #8b6b35; font-size: 11px; }
.job-summary { border-left: 3px solid var(--job-blue); border-radius: 0 10px 10px 0; background: color-mix(in srgb, var(--job-blue-soft) 62%, var(--job-paper)); padding: 13px 16px; }
.skill-list,.welfare-list { display: flex; flex-wrap: wrap; gap: 7px; }
.skill-list span { border: 1px solid color-mix(in srgb, var(--job-blue) 18%, var(--job-line)); border-radius: 6px; background: var(--job-paper); color: var(--job-blue-ink); padding: 5px 8px; font-size: 10px; font-weight: 750; }
.description-section { max-width: 960px; margin-top: 23px; color: var(--job-copy); }
.description-section h4 { display: flex; align-items: center; gap: 9px; margin: 0 0 10px; color: var(--job-ink); font-size: 16px; font-weight: 790; }
.description-section h4::before { width: 4px; height: 14px; border-radius: 3px; background: var(--job-blue); content: ""; }
.description-section p { margin: 0 0 8px; font-size: 13px; line-height: 1.85; }
.job-description { color: var(--job-copy); font-family: inherit; font-size: 13px; line-height: 1.9; white-space: pre-wrap; word-break: break-word; }
.description-empty { color: var(--job-muted); }
.welfare-list span { border-radius: 6px; background: color-mix(in srgb, var(--job-teal) 8%, var(--job-paper)); color: #167777; padding: 6px 9px; font-size: 10px; font-weight: 700; }
.source-link { display: inline-flex; align-items: center; gap: 6px; margin-top: 25px; color: var(--job-blue-ink); font-size: 11px; font-weight: 750; text-decoration: none; }
.source-link:hover { text-decoration: underline; }
.job-browser.is-searching .job-card-list,.job-browser.is-searching .job-detail { opacity: .48; pointer-events: none; }

@keyframes job-spin { to { transform: rotate(360deg); } }
@keyframes search-pulse { from { filter: saturate(.8); opacity: .58; } to { filter: saturate(1.2); opacity: 1; } }
@keyframes status-pulse { from { opacity: .35; } to { opacity: 1; } }

@container job-search (max-width:1000px) {
  .search-rail {
    grid-template-columns:auto minmax(0,1fr) auto;
    gap:10px;
  }

  .search-input-shell {
    grid-column:2 / 4;
  }

  .search-rail > :deep(.job-city-picker) {
    grid-row:2;
    grid-column:2;
    justify-self:start;
  }

  .search-rail > button {
    grid-row:2;
    grid-column:3;
  }

  .job-browser {
    grid-template-columns:320px minmax(0,1fr);
  }

  .detail-hero,
  .company-strip,
  .detail-scroll {
    padding-right:20px;
    padding-left:20px;
  }
}

@container job-search (max-width:760px) {
  .job-workspace-header {
    grid-template-columns:minmax(0,1fr);
    gap:10px;
  }

  .workspace-header-actions {
    flex-wrap:wrap;
  }

  .search-rail {
    grid-template-columns:minmax(0,1fr);
    padding:12px;
  }

  .search-rail > label,
  .search-input-shell,
  .search-rail > :deep(.job-city-picker),
  .search-rail > button {
    grid-row:auto;
    grid-column:1;
    width:100%;
  }

  .job-browser {
    min-height:520px;
    grid-template-columns:minmax(0,1fr);
    grid-template-rows:minmax(0,1fr);
  }

  .job-results,
  .job-detail,
  .detail-empty {
    min-width:0;
    grid-row:1;
    grid-column:1;
  }

  .job-detail {
    display:none;
  }

  .job-browser.show-compact-detail .job-results {
    display:none;
  }

  .job-browser.show-compact-detail .job-detail {
    display:block;
  }

  .detail-back-button {
    display:inline-flex;
    align-items:center;
    margin:12px 14px 0;
  }

  .detail-refreshing {
    top:14px;
    right:14px;
  }

  .detail-hero {
    grid-template-columns:minmax(0,1fr);
    gap:12px;
    padding:16px 16px 14px;
  }

  .title-line {
    align-items:flex-start;
    flex-direction:column;
    gap:7px;
  }

  .title-line h3 {
    overflow-wrap:anywhere;
  }

  .freshness {
    align-items:start;
  }

  .company-strip {
    grid-template-columns:36px minmax(0,1fr);
    padding:12px 16px;
  }

  .company-logo {
    width:36px;
    height:36px;
  }

  .recruiter-online {
    grid-column:2;
  }

  .detail-scroll {
    height:calc(100% - 238px);
    padding:16px 16px 32px;
  }
}

@media (prefers-reduced-motion: reduce) { .button-loader,.search-rail.searching .live-track i,.connection-badge.is-checking i { animation: none; } .job-card,.search-input-shell,.live-track i { transition: none; } }
</style>
