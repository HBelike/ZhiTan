<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import JobSearchWorkspace from './JobSearchWorkspace.vue'
import BossExtensionInstallDialog from './BossExtensionInstallDialog.vue'
import { JobLibraryBridgeError, jobLibraryBridge } from '../job-library-bridge.js'
import {
  BOSS_EXTENSION_VERSION,
  normalizeBossExtensionConnection
} from '../boss-extension-onboarding.js'
import {
  applyGreetingFailure,
  applyGreetingResult,
  createGreetingItems,
  findGreetingFailureAction,
  greetingJobKey,
  needsGreetingRiskWarning,
  normalizeGreetingLimit,
  queueGreetingItems,
  recordGreetingAttempt,
  markGreetingGenerating,
  retryGreetingItems,
  stopGreetingItems,
  updateGreetingItemStatus
} from '../career-greeting-preview.js'
import {
  generateGreetingBatch,
  requestGreeting
} from '../career-greeting-client.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  candidateProfile: { type: Object, default: null },
  previewJobs: { type: Array, default: () => [] },
  initialStage: { type: String, default: 'select' },
  simulationMode: { type: Boolean, default: false }
})

const emit = defineEmits(['cancel'])
const stage = ref('select')
const selectionLimit = ref(5)
const selectedJobs = ref([])
const items = ref([])
const activeItemId = ref('')
const riskOpen = ref(false)
const riskAcknowledged = ref(false)
const sending = ref(false)
const stopped = ref(false)
const stopRequested = ref(false)
const sendError = ref('')
const installDialogOpen = ref(false)
const extensionGateError = ref('')
const extensionGateMode = ref('install')
const regeneratingItemId = ref('')
const generationRunning = ref(false)
const dialogRef = ref(null)
let generationSession = 0

const steps = [
  { key: 'select', label: '选择岗位' },
  { key: 'review', label: '审核招呼语' },
  { key: 'sending', label: '发送进度' }
]

const stageIndex = computed(() => Math.max(0, steps.findIndex((item) => item.key === stage.value)))
const includedItems = computed(() => items.value.filter((item) => (
  item.included
  && Boolean(item.message)
  && !['generating', 'generation_failed'].includes(item.status)
)))
const activeItem = computed(() => items.value.find((item) => item.id === activeItemId.value) ?? items.value[0] ?? null)
const sentCount = computed(() => items.value.filter((item) => item.status === 'sent').length)
const waitingCount = computed(() => items.value.filter((item) => ['queued', 'preflighting', 'sending'].includes(item.status)).length)
const finishedCount = computed(() => items.value.filter((item) => ['sent', 'skipped', 'failed'].includes(item.status)).length)
const sendComplete = computed(() => stage.value === 'sending' && !sending.value && waitingCount.value === 0 && finishedCount.value > 0)
const failureAction = computed(() => findGreetingFailureAction(items.value))
const failureActionItem = computed(() => items.value.find((item) => item.id === failureAction.value?.itemId) ?? null)
const pendingDetailCount = computed(() => selectedJobs.value.filter((job) => !job?.description).length)
const selectedJobsReady = computed(() => selectedJobs.value.length > 0 && pendingDetailCount.value === 0)

watch(() => props.open, (open) => {
  invalidateGeneration()
  if (!open) return
  const previewJobs = props.previewJobs.slice(0, 10)
  const startsInReview = props.initialStage === 'review' && previewJobs.length > 0
  stage.value = startsInReview ? 'review' : 'select'
  selectionLimit.value = Math.max(5, previewJobs.length)
  selectedJobs.value = previewJobs
  items.value = startsInReview ? createGreetingItems(previewJobs) : []
  activeItemId.value = items.value[0]?.id ?? ''
  riskOpen.value = false
  riskAcknowledged.value = false
  sending.value = false
  stopped.value = false
  stopRequested.value = false
  sendError.value = ''
  installDialogOpen.value = false
  extensionGateError.value = ''
  extensionGateMode.value = 'install'
  regeneratingItemId.value = ''
  nextTick(() => dialogRef.value?.focus())
  if (startsInReview) nextTick(() => void generateAllGreetings(previewJobs))
}, { immediate: true })

onBeforeUnmount(invalidateGeneration)

function invalidateGeneration() {
  generationSession += 1
  generationRunning.value = false
  regeneratingItemId.value = ''
}

function close() {
  if (sending.value) {
    stopSending()
    return
  }
  invalidateGeneration()
  emit('cancel')
}

function handleSelectionList(jobs) {
  selectedJobs.value = jobs
  if (selectionLimit.value < jobs.length) selectionLimit.value = jobs.length
}

function updateSelectionLimit(event) {
  const nextLimit = normalizeGreetingLimit(event.target.value)
  selectionLimit.value = Math.max(selectedJobs.value.length, nextLimit)
}

async function startReview() {
  if (!selectedJobsReady.value || !props.candidateProfile?.id || generationRunning.value) return
  items.value = createGreetingItems(selectedJobs.value)
  activeItemId.value = items.value[0]?.id ?? ''
  stage.value = 'review'
  await generateAllGreetings(selectedJobs.value)
}

async function generateAllGreetings(jobs) {
  const session = ++generationSession
  generationRunning.value = true
  await generateGreetingBatch(
    jobs,
    (job) => requestGreeting({
      candidateProfileId: props.candidateProfile?.id,
      job
    }),
    3,
    (result) => {
      if (session !== generationSession) return
      const id = String(result.value?.job_key || greetingJobKey(result.job))
      items.value = result.status === 'fulfilled'
        ? applyGreetingResult(items.value, id, result.value)
        : applyGreetingFailure(items.value, id, result.reason, { preserveMessage: false })
    }
  )
  if (session === generationSession) generationRunning.value = false
}

function returnToSelection() {
  if (sending.value) return
  invalidateGeneration()
  stage.value = 'select'
}

function selectItem(item) {
  activeItemId.value = item.id
}

function updateActiveMessage(event) {
  const message = event.target.value
  items.value = items.value.map((item) => item.id === activeItem.value?.id
    ? { ...item, message }
    : item)
}

function updateActiveIncluded(event) {
  const included = event.target.checked
  items.value = items.value.map((item) => item.id === activeItem.value?.id
    ? {
        ...item,
        included,
        status: included
          ? (item.message ? 'ready' : 'generation_failed')
          : 'excluded'
      }
    : item)
}

async function regenerateActive() {
  const item = activeItem.value
  if (!item || regeneratingItemId.value || generationRunning.value || !props.candidateProfile?.id) return
  regeneratingItemId.value = item.id
  const previousMessage = item.message
  const session = generationSession
  items.value = markGreetingGenerating(items.value, item.id)
  try {
    const payload = await requestGreeting({
      candidateProfileId: props.candidateProfile.id,
      job: item.job,
      previousMessage: previousMessage
    })
    if (session === generationSession) {
      items.value = applyGreetingResult(items.value, item.id, payload)
    }
  } catch (error) {
    if (session === generationSession) {
      items.value = applyGreetingFailure(items.value, item.id, error, { preserveMessage: true })
    }
  } finally {
    if (session === generationSession) regeneratingItemId.value = ''
  }
}

function requestSend() {
  if (!includedItems.value.length) return
  if (needsGreetingRiskWarning(includedItems.value.length)) {
    riskAcknowledged.value = false
    riskOpen.value = true
    return
  }
  startSend()
}

async function startSend() {
  riskOpen.value = false
  if (!await ensureGreetingExtensionReady({ requireDispatchCapability: true })) return
  items.value = queueGreetingItems(items.value)
  stage.value = 'sending'
  sending.value = true
  stopped.value = false
  stopRequested.value = false
  sendError.value = ''
  await runSerialQueue()
}

async function runSerialQueue() {
  while (!stopRequested.value) {
    const nextItem = items.value.find((item) => item.status === 'queued')
    if (!nextItem) break

    activeItemId.value = nextItem.id
    const attemptMode = nextItem.nextAttemptMode === 'message' ? 'message' : 'full'
    items.value = recordGreetingAttempt(items.value, nextItem.id)
    items.value = updateGreetingItemStatus(items.value, nextItem.id, 'preflighting')

    try {
      if (props.simulationMode) {
        await previewDelay(240)
        if (stopRequested.value) break
        items.value = updateGreetingItemStatus(items.value, nextItem.id, 'sending')
        await previewDelay(540)
        items.value = updateGreetingItemStatus(items.value, nextItem.id, 'sent')
        continue
      }

      let result
      if (attemptMode === 'message') {
        items.value = updateGreetingItemStatus(items.value, nextItem.id, 'sending')
        result = await jobLibraryBridge.retryGreetingMessage(nextItem.job, nextItem.message, {
          defaultGreetingSent: nextItem.defaultGreetingSent
        })
      } else {
        items.value = updateGreetingItemStatus(items.value, nextItem.id, 'sending')
        result = await jobLibraryBridge.sendGreeting(nextItem.job, nextItem.message)
      }
      const nextStatus = result?.status === 'skipped' ? 'skipped' : 'sent'
      items.value = updateGreetingItemStatus(items.value, nextItem.id, nextStatus, result?.reason ?? '', {
        sentAt: result?.sentAt ?? '',
        defaultGreetingSent: result?.defaultGreetingSent === true || nextItem.defaultGreetingSent === true,
        retryMode: '',
        nextAttemptMode: 'full',
        retryable: false
      })
    } catch (error) {
      const code = error instanceof JobLibraryBridgeError ? error.code : 'unknown_error'
      const message = error instanceof Error ? error.message : '发送失败，请检查 BOSS 页面状态。'
      if (['job_unavailable', 'already_contacted'].includes(code)) {
        items.value = updateGreetingItemStatus(items.value, nextItem.id, 'skipped', message)
        continue
      }
      const retryable = error instanceof JobLibraryBridgeError && error.retryable
      items.value = updateGreetingItemStatus(items.value, nextItem.id, 'failed', message, {
        errorCode: code,
        retryable,
        retryMode: error instanceof JobLibraryBridgeError ? error.retryMode : '',
        submissionState: error instanceof JobLibraryBridgeError ? error.submissionState : '',
        defaultGreetingSent: error instanceof JobLibraryBridgeError && error.defaultGreetingSent,
        failedAt: new Date().toISOString()
      })
      sendError.value = message
    }
  }

  if (stopRequested.value) {
    items.value = stopGreetingItems(items.value)
    const failedItem = items.value.find((item) => item.status === 'failed')
    if (failedItem) activeItemId.value = failedItem.id
    stopped.value = true
  }
  sending.value = false
}

async function retryFailedItem(item) {
  if (sending.value || !item?.retryable) return
  if (!await ensureGreetingExtensionReady({ requireRetryCapability: true })) return
  items.value = retryGreetingItems(items.value, item.id)
  activeItemId.value = item.id
  sending.value = true
  stopped.value = false
  stopRequested.value = false
  sendError.value = ''
  await runSerialQueue()
}

async function ensureGreetingExtensionReady({ requireRetryCapability = false, requireDispatchCapability = false } = {}) {
  if (props.simulationMode) return true
  try {
    const connection = normalizeBossExtensionConnection(await jobLibraryBridge.ping())
    const supportsRequestedAction = (!requireRetryCapability || connection.greetingRetryReady)
      && (!requireDispatchCapability || connection.greetingDispatchReady)
    if (connection.status === 'ready' && supportsRequestedAction) return true
    extensionGateMode.value = connection.status === 'ready' ? 'update' : 'install'
    extensionGateError.value = connection.status === 'ready'
      ? `当前浏览器助手${connection.version ? ` v${connection.version}` : ''}${requireDispatchCapability ? '不支持触发发送后直接继续下一条' : '不支持安全重试'}，请更新到 v${BOSS_EXTENSION_VERSION}。`
      : '未检测到浏览器助手，请安装后再发送。'
  } catch (error) {
    extensionGateMode.value = 'install'
    extensionGateError.value = error instanceof Error ? error.message : '未检测到浏览器助手，请安装后再发送。'
  }
  sendError.value = extensionGateError.value
  installDialogOpen.value = true
  return false
}

async function handleFailureAction() {
  const item = failureActionItem.value
  if (!item || sending.value) return
  activeItemId.value = item.id
  if (failureAction.value?.type === 'retry') {
    await retryFailedItem(item)
    return
  }
  extensionGateMode.value = 'update'
  extensionGateError.value = `这条失败记录来自旧版助手，无法确认提交阶段。请更新到 v${BOSS_EXTENSION_VERSION} 后重新发起。`
  installDialogOpen.value = true
}

function refreshForExtensionCheck() {
  window.location.reload()
}

function stopSending() {
  stopRequested.value = true
  items.value = stopGreetingItems(items.value)
  stopped.value = true
}

function previewDelay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function statusLabel(status) {
  return {
    ready: '待审核',
    generating: '智能生成中',
    generation_failed: '生成失败',
    excluded: '已取消',
    queued: '等待发送',
    preflighting: '正在预检',
    sending: '正在发送',
    sent: '已触发发送',
    skipped: '已跳过',
    failed: '发送失败',
    stopped: '已停止'
  }[status] ?? '等待处理'
}

function formatStatusTime(value) {
  if (!value) return '尚未处理'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '时间未知'
  return parsed.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  })
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="greeting-backdrop" @mousedown.self="close" @keydown.esc="close">
      <section
        ref="dialogRef"
        class="greeting-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="greeting-dialog-title"
        tabindex="-1"
      >
        <header class="greeting-header">
          <div class="greeting-title-block">
            <div class="greeting-title-row">
              <h2 id="greeting-dialog-title">一键打招呼</h2>
              <span class="preview-badge" :class="{ live: !simulationMode }"><i aria-hidden="true"></i>{{ simulationMode ? '安全预览' : '真实发送' }}</span>
            </div>
            <p>从职位库选岗，逐条确认招呼语，再按顺序发送。</p>
          </div>
          <div class="greeting-header-meta">
            <span class="resume-version">简历 v{{ candidateProfile?.version ?? 1 }}</span>
            <button type="button" class="close-button" aria-label="关闭一键打招呼" @click="close">×</button>
          </div>
        </header>

        <nav class="greeting-steps" aria-label="一键打招呼步骤">
          <template v-for="(item, index) in steps" :key="item.key">
            <span class="step-item" :class="{ active: index === stageIndex, complete: index < stageIndex }">
              <i>{{ index < stageIndex ? '✓' : index + 1 }}</i>{{ item.label }}
            </span>
            <span v-if="index < steps.length - 1" class="step-line" :class="{ complete: index < stageIndex }" aria-hidden="true"></span>
          </template>
        </nav>

        <main class="greeting-body" :class="`stage-${stage}`">
          <section v-show="stage === 'select'" class="selection-stage">
            <JobSearchWorkspace
              multi-selection-mode
              :selection-limit="selectionLimit"
              @selection-list-change="handleSelectionList"
            />
          </section>

          <section v-if="stage === 'review'" class="review-stage">
            <aside class="delivery-track" aria-label="本批岗位顺序">
              <header>
                <span>投递轨道</span>
                <strong>{{ includedItems.length }}/{{ items.length }}</strong>
              </header>
              <div class="track-list">
                <button
                  v-for="item in items"
                  :key="item.id"
                  type="button"
                  class="track-item"
                  :class="[item.status, { active: activeItem?.id === item.id }]"
                  @click="selectItem(item)"
                >
                  <span class="track-node" aria-hidden="true"><i></i></span>
                  <span class="track-copy">
                    <strong>{{ item.job.title }}</strong>
                    <small>{{ item.job.company || item.job.companyShort }}</small>
                  </span>
                  <em>{{ statusLabel(item.status) }}</em>
                </button>
              </div>
              <p>顺序就是发送顺序。取消的岗位会留在轨道上，但不会进入发送队列。</p>
            </aside>

            <article v-if="activeItem" class="message-editor">
              <header class="editor-heading">
                <div>
                  <span>{{ activeItem.job.company || activeItem.job.companyShort }}</span>
                  <h3>{{ activeItem.job.title }}</h3>
                </div>
                <label class="include-switch">
                  <input type="checkbox" :checked="activeItem.included" @change="updateActiveIncluded" />
                  <span>本批发送</span>
                </label>
              </header>

              <div class="message-paper" :class="{ disabled: !activeItem.included }">
                <label for="greeting-message">招呼语</label>
                <div v-if="activeItem.status === 'generating' && !activeItem.message" class="generation-placeholder" role="status">
                  <span class="button-loader" aria-hidden="true"></span>
                  DeepSeek V4 Pro 正在结合简历与完整 JD 生成
                </div>
                <textarea
                  id="greeting-message"
                  :value="activeItem.message"
                  :disabled="!activeItem.included || activeItem.status === 'generating' || !activeItem.message"
                  @input="updateActiveMessage"
                ></textarea>
                <p v-if="activeItem.generationError" class="generation-error" role="alert">
                  {{ activeItem.generationError }}
                </p>
                <div class="message-meta">
                  <span>建议 150 字以内，不做机械截断</span>
                  <strong>{{ activeItem.message?.length || 0 }} 字</strong>
                </div>
              </div>

              <div class="editor-actions">
                <button type="button" class="regenerate-button" :disabled="!activeItem.included || activeItem.status === 'generating' || generationRunning || regeneratingItemId === activeItem.id" @click="regenerateActive">
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/></svg>
                  {{ regeneratingItemId === activeItem.id ? '正在重新生成' : (activeItem.message ? '重新生成这一条' : '重试生成这一条') }}
                </button>
                <span v-if="activeItem.revision">第 {{ activeItem.revision }} 版 · 事实证据已校验</span>
                <span v-else>尚未生成成功</span>
              </div>

              <section v-if="activeItem.message && activeItem.status !== 'generating'" class="quality-note">
                <i aria-hidden="true">✓</i>
                <div><strong>事实约束已通过</strong><p>数字、公司、项目和经历均来自当前简历版本。</p></div>
              </section>
            </article>

            <aside v-if="activeItem" class="evidence-panel">
              <header><span>写作依据</span><small>不补写经历</small></header>
              <div class="evidence-groups">
                <section class="evidence-section resume-evidence">
                  <header class="evidence-section-header">
                    <h4 id="resume-evidence-title">简历证据</h4>
                    <span>{{ activeItem.evidence.length }} 条</span>
                  </header>
                  <div class="evidence-scroll" role="region" aria-labelledby="resume-evidence-title" tabindex="0">
                    <ul v-if="activeItem.evidence.length">
                      <li v-for="evidence in activeItem.evidence" :key="evidence">{{ evidence }}</li>
                    </ul>
                    <p v-else class="evidence-empty">本条招呼语未引用简历证据</p>
                  </div>
                </section>
                <section class="evidence-section job-highlights">
                  <header class="evidence-section-header">
                    <h4 id="job-highlights-title">岗位关注点</h4>
                    <span>{{ activeItem.jdHighlights.length }} 条</span>
                  </header>
                  <div class="evidence-scroll" role="region" aria-labelledby="job-highlights-title" tabindex="0">
                    <ul v-if="activeItem.jdHighlights.length" class="highlight-list">
                      <li v-for="highlight in activeItem.jdHighlights" :key="highlight">{{ highlight }}</li>
                    </ul>
                    <p v-else class="evidence-empty">暂未提取岗位关注点</p>
                  </div>
                </section>
              </div>
              <footer><strong>DeepSeek V4 Pro</strong><span>基于简历与完整 JD 生成，固定 temperature=0.2。</span></footer>
            </aside>
          </section>

          <section v-if="stage === 'sending'" class="sending-stage">
            <aside class="delivery-track sending-track" :aria-label="simulationMode ? '本地模拟发送顺序' : '真实串行发送顺序'">
              <header><span>投递轨道</span><strong>{{ finishedCount }}/{{ includedItems.length }}</strong></header>
              <div class="track-list">
                <button
                  v-for="item in items"
                  :key="item.id"
                  type="button"
                  class="track-item"
                  :class="[item.status, { active: activeItem?.id === item.id }]"
                  @click="selectItem(item)"
                >
                  <span class="track-node" aria-hidden="true"><i></i></span>
                  <span class="track-copy"><strong>{{ item.job.title }}</strong><small>{{ item.job.company || item.job.companyShort }}</small></span>
                  <em>{{ statusLabel(item.status) }}</em>
                </button>
              </div>
            </aside>

            <section class="progress-board">
              <div class="progress-kicker">{{ simulationMode ? '本地发送演示' : 'BOSS 真实发送' }}</div>
              <h3 v-if="sending">正在按顺序处理第 {{ finishedCount + 1 }} 条</h3>
              <h3 v-else-if="stopped">已停止剩余发送</h3>
              <h3 v-else>{{ simulationMode ? '本批模拟发送完成' : '本批真实发送完成' }}</h3>
              <p v-if="sending">{{ simulationMode ? '当前页面只展示未来的状态变化，不会连接或操作 BOSS。' : '每次只处理一个岗位，触发本条发送后立即继续下一条，不等待送达回执。' }}</p>
              <p v-else-if="stopped">已完成的记录保留，未开始的岗位已标记为停止。</p>
              <p v-else>{{ simulationMode ? '所有已纳入本批的岗位均已完成本地状态演示。' : '所有已纳入本批的岗位均已完成处理。' }}</p>

              <p v-if="sendError" class="send-error" role="alert">{{ sendError }}</p>

              <section v-if="failureActionItem" class="failure-action-bar" role="status">
                <div>
                  <strong>{{ failureAction?.type === 'retry' ? '失败项可以安全重试' : '需要更新浏览器助手' }}</strong>
                  <span>{{ failureActionItem.job.title }} · {{ failureActionItem.job.company || failureActionItem.job.companyShort }}</span>
                </div>
                <button type="button" :disabled="sending" @click="handleFailureAction">
                  {{ failureAction?.type === 'retry' ? '重新发送失败项' : '更新助手后重试' }}
                </button>
              </section>

              <div class="progress-rail" role="progressbar" :aria-valuenow="finishedCount" :aria-valuemax="includedItems.length">
                <i :style="{ width: `${includedItems.length ? (finishedCount / includedItems.length) * 100 : 0}%` }"></i>
              </div>
              <div class="progress-numbers"><strong>{{ sentCount }}</strong><span>已完成</span><b>{{ waitingCount }}</b><span>待处理</span></div>

              <article v-if="activeItem" class="current-send-card">
                <span class="company-avatar">{{ (activeItem.job.companyShort || activeItem.job.company || '职').slice(0, 1) }}</span>
                <div><small>当前岗位</small><strong>{{ activeItem.job.company || activeItem.job.companyShort }} · {{ activeItem.job.title }}</strong><p>{{ activeItem.message }}</p></div>
              </article>

              <section v-if="activeItem" class="send-status-record" :class="activeItem.status">
                <header><span>本条发送记录</span><strong>{{ statusLabel(activeItem.status) }}</strong></header>
                <dl>
                  <div><dt>尝试次数</dt><dd>{{ activeItem.attemptCount || 0 }} 次</dd></div>
                  <div><dt>最近处理</dt><dd>{{ formatStatusTime(activeItem.sentAt || activeItem.failedAt || activeItem.lastAttemptAt) }}</dd></div>
                  <div><dt>默认招呼</dt><dd>{{ activeItem.defaultGreetingSent ? 'BOSS 已发送' : '未记录' }}</dd></div>
                </dl>
                <p v-if="activeItem.error">{{ activeItem.error }}</p>
                <small v-if="activeItem.status === 'failed' && activeItem.submissionState === 'unknown'">发送结果无法确认，为避免重复消息，本条不可重试。</small>
              </section>
            </section>

            <aside class="simulation-boundary" :class="{ live: !simulationMode }">
              <span class="boundary-mark">{{ simulationMode ? '本地' : 'BOSS' }}</span>
              <h4>{{ simulationMode ? '不会执行真实发送' : '真实串行发送' }}</h4>
              <p>{{ simulationMode ? '本页未调用浏览器扩展的发送动作，也不会向 BOSS 提交消息。' : '每条发送前检查登录、验证与岗位状态；单条异常会记录失败并继续后续岗位。' }}</p>
              <ul v-if="simulationMode"><li>登录与验证码状态未检查</li><li>账号额度未读取</li><li>所有结果仅保存在当前页面</li></ul>
              <ul v-else><li>同一时间只发送一条</li><li>点击发送后不等待送达回执</li><li>不绕过验证码或平台限制</li></ul>
            </aside>
          </section>
        </main>

        <footer class="greeting-footer">
          <div v-if="stage === 'select'" class="batch-limit-control">
            <label for="greeting-limit">本批上限</label>
            <input id="greeting-limit" :value="selectionLimit" type="number" min="1" max="10" @change="updateSelectionLimit" />
            <span>最多 10 个，当前已选 <strong>{{ selectedJobs.length }}</strong> 个</span>
            <small v-if="!candidateProfile?.id">请先在当前会话选择简历资料</small>
          </div>
          <div v-else class="local-boundary" :class="{ live: !simulationMode }">
            <i aria-hidden="true"></i><span>{{ simulationMode ? '安全预览，不会发送到 BOSS' : '真实发送：逐条触发、不等待送达回执' }}</span>
          </div>

          <div class="footer-actions">
            <button v-if="stage === 'review'" type="button" class="secondary-button" @click="returnToSelection">返回选岗</button>
            <button v-if="stage === 'sending' && sending" type="button" class="danger-button" @click="stopSending">停止剩余发送</button>
            <button type="button" class="secondary-button" @click="close">{{ sendComplete || stopped ? '关闭' : '取消' }}</button>
            <button v-if="stage === 'select'" type="button" class="primary-button" :disabled="!selectedJobsReady || !candidateProfile?.id || generationRunning" @click="startReview">
              {{ pendingDetailCount ? `正在准备 ${pendingDetailCount} 个岗位详情` : `生成 ${selectedJobs.length || ''} 条招呼语` }}
            </button>
            <button v-else-if="stage === 'review'" type="button" class="primary-button" :disabled="!includedItems.length || generationRunning" @click="requestSend">确认并{{ simulationMode ? '模拟' : '真实' }}发送 {{ includedItems.length }} 条</button>
          </div>
        </footer>

        <div v-if="riskOpen" class="risk-layer" @mousedown.self="riskOpen = false">
          <section class="risk-dialog" role="alertdialog" aria-modal="true" aria-labelledby="greeting-risk-title">
            <span class="risk-mark" aria-hidden="true">!</span>
            <div><small>发送前确认</small><h3 id="greeting-risk-title">本批包含 {{ includedItems.length }} 个岗位</h3></div>
            <p>连续向多个岗位发起沟通可能触发 BOSS 限流、安全验证、沟通额度限制或账号封禁。为降低集中并发风险，正式功能会保持逐岗串行；单岗异常只记录失败并继续，不会尝试绕过平台限制。</p>
            <label><input v-model="riskAcknowledged" type="checkbox" /><span>我已了解批量发送可能带来的账号风险</span></label>
            <footer><button type="button" class="secondary-button" @click="riskOpen = false">返回检查</button><button type="button" class="risk-confirm-button" :disabled="!riskAcknowledged" @click="startSend">了解风险，开始{{ simulationMode ? '模拟' : '真实发送' }}</button></footer>
          </section>
        </div>

        <BossExtensionInstallDialog
          :open="installDialogOpen"
          :error="extensionGateError"
          :mode="extensionGateMode"
          @close="installDialogOpen = false"
          @refresh="refreshForExtensionCheck"
        />
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.greeting-backdrop {
  position: fixed;
  z-index: 1580;
  inset: 0;
  display: grid;
  place-items: center;
  box-sizing: border-box;
  background: rgba(8, 28, 58, .58);
  padding: 24px;
  backdrop-filter: blur(8px);
}

.greeting-dialog {
  --greeting-canvas: var(--ui-canvas, #f3f7fc);
  --greeting-paper: var(--ui-surface, #fff);
  --greeting-ink: var(--ui-text, #14213d);
  --greeting-copy: var(--ui-text-secondary, #526078);
  --greeting-muted: var(--ui-text-muted, #7b879c);
  --greeting-line: var(--ui-line, #d7e4f6);
  --greeting-line-strong: var(--ui-line-strong, #b9cfed);
  --greeting-blue: var(--ui-accent, #0869d8);
  --greeting-blue-ink: var(--ui-accent-ink, #004aa8);
  --greeting-blue-soft: var(--ui-surface-active, #eaf3ff);
  --greeting-teal: #04a6a6;
  --greeting-risk: #c94955;
  position: relative;
  display: grid;
  width: min(1560px, calc(100vw - 48px));
  height: 80dvh;
  min-height: 0;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  overflow: hidden;
  border: 1px solid var(--greeting-line-strong);
  border-radius: 22px;
  outline: none;
  background: var(--greeting-paper);
  box-shadow: 0 32px 100px rgba(5, 35, 78, .34);
  color: var(--greeting-ink);
  font-family: var(--ui-font-body, "Segoe UI Variable Text", "PingFang SC", sans-serif);
  animation: greeting-dialog-in .18s ease-out;
}

.greeting-header { display: flex; min-height: 76px; align-items: center; justify-content: space-between; gap: 24px; border-bottom: 1px solid var(--greeting-line); background: linear-gradient(135deg, #fff 56%, var(--greeting-blue-soft)); padding: 14px 20px 13px 24px; }
.greeting-title-row,.greeting-header-meta,.preview-badge,.resume-version,.step-item,.local-boundary { display: flex; align-items: center; }
.greeting-title-row { gap: 10px; }
.greeting-title-block h2 { margin: 0; font: 850 23px/1.15 var(--ui-font-display, "Segoe UI Variable Display", sans-serif); letter-spacing: -.035em; }
.greeting-title-block p { margin: 5px 0 0; color: var(--greeting-muted); font-size: 11px; }
.preview-badge { gap: 6px; border: 1px solid rgba(4,166,166,.25); border-radius: 999px; background: #edf9f7; color: #087d7d; padding: 4px 8px; font: 800 9px/1 var(--ui-font-utility, Consolas, monospace); }
.preview-badge.live { border-color: rgba(8,105,216,.25); background: var(--greeting-blue-soft); color: var(--greeting-blue-ink); }
.preview-badge.live i { background: var(--greeting-blue); box-shadow: 0 0 0 3px rgba(8,105,216,.13); }
.preview-badge i,.local-boundary i { width: 6px; height: 6px; border-radius: 50%; background: var(--greeting-teal); box-shadow: 0 0 0 3px rgba(4,166,166,.13); }
.greeting-header-meta { gap: 10px; }
.resume-version { min-height: 32px; border: 1px solid var(--greeting-line); border-radius: 9px; background: #fff; color: var(--greeting-copy); padding: 0 10px; font: 800 10px/1 var(--ui-font-utility, Consolas, monospace); }
.close-button { display: grid; width: 36px; height: 36px; place-items: center; border: 1px solid var(--greeting-line); border-radius: 10px; background: #fff; color: var(--greeting-copy); cursor: pointer; font-size: 24px; line-height: 1; }
.close-button:hover,.close-button:focus-visible { border-color: var(--greeting-blue); color: var(--greeting-blue-ink); outline: 0; box-shadow: 0 0 0 3px var(--ui-focus, rgba(8,105,216,.14)); }

.greeting-steps { display: flex; min-height: 46px; align-items: center; justify-content: center; border-bottom: 1px solid var(--greeting-line); background: #fbfdff; padding: 0 24px; }
.step-item { gap: 7px; color: var(--greeting-muted); font-size: 11px; font-weight: 800; white-space: nowrap; }
.step-item i { display: grid; width: 22px; height: 22px; place-items: center; border: 1px solid var(--greeting-line-strong); border-radius: 50%; background: #fff; color: var(--greeting-muted); font: 800 9px/1 var(--ui-font-utility, Consolas, monospace); }
.step-item.active { color: var(--greeting-blue-ink); }
.step-item.active i { border-color: var(--greeting-blue); background: var(--greeting-blue); color: #fff; box-shadow: 0 0 0 4px rgba(8,105,216,.09); }
.step-item.complete { color: #087d7d; }
.step-item.complete i { border-color: var(--greeting-teal); background: #edf9f7; color: #087d7d; }
.step-line { width: clamp(64px, 8vw, 140px); height: 1px; margin: 0 13px; background: var(--greeting-line); }
.step-line.complete { background: color-mix(in srgb, var(--greeting-teal) 55%, var(--greeting-line)); }

.greeting-body { min-height: 0; overflow: hidden; background: var(--greeting-canvas); }
.selection-stage { display: flex; height: 100%; min-height: 0; box-sizing: border-box; padding: 14px 16px; }
.selection-stage :deep(.job-workspace) { width: 100%; }
.selection-stage :deep(.job-browser) { border-radius: 12px; }

.review-stage,.sending-stage { display: grid; height: 100%; min-height: 0; grid-template-columns: 300px minmax(520px, 1fr) 280px; overflow: hidden; background: var(--greeting-paper); }
.delivery-track { min-width: 0; overflow: hidden; border-right: 1px solid var(--greeting-line); background: color-mix(in srgb, var(--greeting-canvas) 64%, #fff); }
.delivery-track > header { display: flex; min-height: 54px; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--greeting-line); padding: 0 16px 0 19px; }
.delivery-track > header span { font-size: 12px; font-weight: 850; }
.delivery-track > header strong { border-radius: 999px; background: var(--greeting-blue-soft); color: var(--greeting-blue-ink); padding: 5px 8px; font: 850 10px/1 var(--ui-font-utility, Consolas, monospace); }
.delivery-track > p { margin: 0; border-top: 1px solid var(--greeting-line); color: var(--greeting-muted); padding: 12px 16px; font-size: 10px; line-height: 1.55; }
.track-list { height: calc(100% - 116px); overflow-y: auto; padding: 10px 10px 12px 15px; }
.track-item { position: relative; display: grid; width: 100%; grid-template-columns: 22px minmax(0, 1fr) auto; align-items: center; gap: 9px; border: 1px solid transparent; border-radius: 11px; background: transparent; color: var(--greeting-ink); padding: 10px 8px 10px 4px; cursor: pointer; text-align: left; }
.track-item::before { position: absolute; top: -10px; bottom: calc(50% + 10px); left: 14px; width: 1px; background: var(--greeting-line-strong); content: ""; }
.track-item:first-child::before { display: none; }
.track-item:hover,.track-item:focus-visible,.track-item.active { border-color: var(--greeting-line-strong); outline: 0; background: #fff; }
.track-item.active { box-shadow: 0 7px 18px rgba(8,75,157,.08); }
.track-node { position: relative; z-index: 1; display: grid; width: 20px; height: 20px; place-items: center; }
.track-node i { width: 8px; height: 8px; border: 3px solid var(--greeting-canvas); border-radius: 50%; background: var(--greeting-line-strong); box-shadow: 0 0 0 1px var(--greeting-line-strong); }
.track-item.active .track-node i,.track-item.ready .track-node i { background: var(--greeting-blue); box-shadow: 0 0 0 1px var(--greeting-blue); }
.track-item.sent .track-node i { background: var(--greeting-teal); box-shadow: 0 0 0 1px var(--greeting-teal); }
.track-item.sending .track-node i,.track-item.preflighting .track-node i { background: var(--greeting-blue); box-shadow: 0 0 0 4px rgba(8,105,216,.15); animation: track-pulse .75s ease-in-out infinite alternate; }
.track-item.excluded .track-node i,.track-item.stopped .track-node i,.track-item.skipped .track-node i { background: #aeb9c8; box-shadow: 0 0 0 1px #aeb9c8; }
.track-item.failed .track-node i { background: var(--greeting-risk); box-shadow: 0 0 0 1px var(--greeting-risk); }
.track-copy { min-width: 0; }
.track-copy strong,.track-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.track-copy strong { font-size: 12px; }
.track-copy small { margin-top: 3px; color: var(--greeting-muted); font-size: 10px; }
.track-item em { color: var(--greeting-muted); font-size: 9px; font-style: normal; font-weight: 750; white-space: nowrap; }
.track-item.sent em { color: #087d7d; }
.track-item.sending em,.track-item.preflighting em { color: var(--greeting-blue-ink); }
.track-item.failed em { color: var(--greeting-risk); }

.message-editor { min-width: 0; overflow-y: auto; background: #fff; padding: 26px 30px; }
.editor-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.editor-heading span { color: var(--greeting-blue-ink); font-size: 10px; font-weight: 850; }
.editor-heading h3 { margin: 5px 0 0; font: 850 24px/1.2 var(--ui-font-display, "Segoe UI Variable Display", sans-serif); letter-spacing: -.03em; }
.include-switch { display: flex; align-items: center; gap: 7px; border: 1px solid var(--greeting-line); border-radius: 999px; background: var(--greeting-blue-soft); color: var(--greeting-blue-ink); padding: 7px 10px; font-size: 10px; font-weight: 850; }
.include-switch input { margin: 0; accent-color: var(--greeting-blue); }
.message-paper { margin-top: 22px; border: 1px solid var(--greeting-line-strong); border-radius: 16px; background: linear-gradient(#fff,#fbfdff); padding: 16px 17px 12px; box-shadow: 0 14px 34px rgba(8,65,133,.07); transition: opacity .16s ease; }
.message-paper.disabled { opacity: .5; }
.message-paper > label { color: var(--greeting-copy); font-size: 10px; font-weight: 850; }
.generation-placeholder { display: flex; min-height: 46px; align-items: center; gap: 9px; margin-top: 10px; border-radius: 9px; background: var(--greeting-blue-soft); color: var(--greeting-blue-ink); padding: 9px 11px; font-size: 11px; font-weight: 750; }
.generation-error { margin: 9px 0 0; border-radius: 8px; background: #fff1f2; color: #a33b46; padding: 8px 10px; font-size: 10px; line-height: 1.55; }
.message-paper textarea { display: block; width: 100%; min-height: 178px; box-sizing: border-box; margin-top: 9px; resize: vertical; border: 0; outline: 0; background: transparent; color: var(--greeting-ink); padding: 0; font: 520 15px/1.9 var(--ui-font-body, "Segoe UI Variable Text", sans-serif); }
.message-paper:focus-within { border-color: var(--greeting-blue); box-shadow: 0 0 0 3px var(--ui-focus, rgba(8,105,216,.12)),0 14px 34px rgba(8,65,133,.07); }
.message-meta { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-top: 1px dashed var(--greeting-line); color: var(--greeting-muted); padding-top: 9px; font-size: 9px; }
.message-meta strong { color: var(--greeting-copy); font: 800 10px/1 var(--ui-font-utility, Consolas, monospace); }
.editor-actions { display: flex; min-height: 48px; align-items: center; justify-content: space-between; gap: 14px; border-bottom: 1px solid var(--greeting-line); }
.regenerate-button { display: inline-flex; align-items: center; gap: 7px; border: 0; background: transparent; color: var(--greeting-blue-ink); padding: 7px 0; cursor: pointer; font-size: 11px; font-weight: 850; }
.regenerate-button svg { width: 15px; fill: none; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.8; }
.regenerate-button:hover:not(:disabled),.regenerate-button:focus-visible { color: var(--greeting-blue); outline: 0; text-decoration: underline; }
.regenerate-button:disabled { cursor: wait; opacity: .5; }
.editor-actions > span { color: var(--greeting-muted); font: 700 9px/1 var(--ui-font-utility, Consolas, monospace); }
.quality-note { display: grid; grid-template-columns: 30px minmax(0,1fr); align-items: center; gap: 10px; margin-top: 18px; border-left: 3px solid var(--greeting-teal); border-radius: 0 12px 12px 0; background: #f0faf8; padding: 12px 13px; }
.quality-note > i { display: grid; width: 28px; height: 28px; place-items: center; border-radius: 50%; background: #dff5f1; color: #087d7d; font-size: 12px; font-style: normal; font-weight: 900; }
.quality-note strong,.quality-note p { display: block; }
.quality-note strong { color: #146f70; font-size: 11px; }
.quality-note p { margin: 3px 0 0; color: #5b7e7d; font-size: 10px; }

.evidence-panel { display: grid; min-width: 0; min-height: 0; grid-template-rows: auto minmax(0,1fr) auto; gap: 12px; overflow: hidden; border-left: 1px solid var(--greeting-line); background: #fbfdff; padding: 18px 14px 14px; }
.evidence-panel > header { display: flex; align-items: center; justify-content: space-between; gap: 10px; border-bottom: 1px solid var(--greeting-line); padding-bottom: 12px; }
.evidence-panel > header span { font-size: 12px; font-weight: 850; }
.evidence-panel > header small { color: var(--greeting-muted); font-size: 9px; }
.evidence-groups { display: grid; min-height: 0; grid-template-rows: repeat(2,minmax(0,1fr)); gap: 12px; }
.evidence-section { display: grid; min-height: 0; grid-template-rows: auto minmax(0,1fr); overflow: hidden; border: 1px solid var(--greeting-line); border-radius: 11px; background: #fff; box-shadow: 0 5px 14px rgba(8,65,133,.035); }
.evidence-section-header { display: flex; min-height: 38px; align-items: center; justify-content: space-between; gap: 8px; border-bottom: 1px solid var(--greeting-line); padding: 0 10px 0 12px; }
.evidence-section h4 { margin: 0; color: var(--greeting-ink); font-size: 10px; }
.evidence-section-header > span { border-radius: 999px; background: var(--greeting-canvas); color: var(--greeting-muted); padding: 4px 7px; font: 800 8px/1 var(--ui-font-utility, Consolas, monospace); }
.resume-evidence .evidence-section-header { box-shadow: inset 3px 0 0 var(--greeting-teal); }
.job-highlights .evidence-section-header { box-shadow: inset 3px 0 0 var(--greeting-blue); }
.evidence-scroll { min-height: 0; overflow-y: auto; padding: 9px 6px 9px 9px; scrollbar-color: var(--greeting-line-strong) transparent; scrollbar-gutter: stable; scrollbar-width: thin; }
.evidence-scroll::-webkit-scrollbar { width: 6px; }
.evidence-scroll::-webkit-scrollbar-track { background: transparent; }
.evidence-scroll::-webkit-scrollbar-thumb { border: 1px solid transparent; border-radius: 999px; background: var(--greeting-line-strong); background-clip: padding-box; }
.evidence-scroll:focus-visible { outline: 2px solid var(--greeting-blue); outline-offset: -2px; }
.evidence-scroll ul { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.resume-evidence li { position: relative; border: 1px solid var(--greeting-line); border-radius: 9px; background: #fff; color: var(--greeting-copy); padding: 8px 8px 8px 23px; font-size: 10px; line-height: 1.45; }
.resume-evidence li::before { position: absolute; top: 11px; left: 9px; width: 6px; height: 6px; border-radius: 50%; background: var(--greeting-teal); content: ""; }
.highlight-list li { border-radius: 8px; background: var(--greeting-blue-soft); color: var(--greeting-blue-ink); padding: 7px 9px; font-size: 9px; font-weight: 800; line-height: 1.45; }
.evidence-empty { margin: 0; color: var(--greeting-muted); padding: 9px 5px; font-size: 9px; line-height: 1.5; }
.evidence-panel > footer { border-top: 1px solid var(--greeting-line); padding: 11px 2px 0; }
.evidence-panel > footer strong,.evidence-panel > footer span { display: block; }
.evidence-panel > footer strong { color: var(--greeting-blue-ink); font: 850 10px/1 var(--ui-font-utility, Consolas, monospace); }
.evidence-panel > footer span { margin-top: 5px; color: var(--greeting-muted); font-size: 9px; line-height: 1.55; }

.progress-board { min-width: 0; overflow-y: auto; background: #fff; padding: 46px 52px; }
.progress-kicker { color: var(--greeting-blue-ink); font: 850 10px/1 var(--ui-font-utility, Consolas, monospace); }
.progress-board h3 { margin: 12px 0 0; font: 880 28px/1.2 var(--ui-font-display, "Segoe UI Variable Display", sans-serif); letter-spacing: -.035em; }
.progress-board > p { margin: 9px 0 0; color: var(--greeting-muted); font-size: 12px; }
.progress-board > .send-error { border-left: 3px solid var(--greeting-risk); border-radius: 0 8px 8px 0; background: #fff3f4; color: #a93c47; padding: 9px 11px; line-height: 1.55; }
.failure-action-bar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 12px; border: 1px solid #efc8cc; border-radius: 11px; background: #fffafb; padding: 11px 12px; }
.failure-action-bar div { min-width: 0; }
.failure-action-bar strong,.failure-action-bar span { display: block; }
.failure-action-bar strong { color: #a93c47; font-size: 11px; }
.failure-action-bar span { margin-top: 3px; overflow: hidden; color: var(--greeting-copy); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.failure-action-bar button { flex: none; border: 1px solid var(--greeting-blue); border-radius: 9px; background: var(--greeting-blue); color: #fff; padding: 9px 12px; cursor: pointer; font-size: 10px; font-weight: 850; }
.failure-action-bar button:hover,.failure-action-bar button:focus-visible { outline: 0; background: var(--greeting-blue-ink); box-shadow: 0 0 0 3px var(--ui-focus,rgba(8,105,216,.14)); }
.failure-action-bar button:disabled { cursor: wait; opacity: .55; }
.progress-rail { height: 8px; overflow: hidden; margin-top: 34px; border-radius: 999px; background: var(--greeting-blue-soft); }
.progress-rail i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg,var(--greeting-blue),var(--greeting-teal)); transition: width .35s ease; }
.progress-numbers { display: grid; grid-template-columns: auto auto auto auto 1fr; align-items: baseline; gap: 7px; margin-top: 12px; }
.progress-numbers strong,.progress-numbers b { font: 900 20px/1 var(--ui-font-display, sans-serif); }
.progress-numbers strong { color: var(--greeting-blue-ink); }
.progress-numbers b { margin-left: 18px; color: var(--greeting-muted); }
.progress-numbers span { color: var(--greeting-muted); font-size: 9px; }
.current-send-card { display: grid; grid-template-columns: 48px minmax(0,1fr); gap: 13px; margin-top: 36px; border: 1px solid var(--greeting-line); border-radius: 16px; background: #fbfdff; padding: 17px; box-shadow: 0 12px 30px rgba(8,65,133,.06); }
.company-avatar { display: grid; width: 46px; height: 46px; place-items: center; border-radius: 14px; background: var(--greeting-blue); color: #fff; font-size: 17px; font-weight: 900; }
.current-send-card small,.current-send-card strong { display: block; }
.current-send-card small { color: var(--greeting-muted); font-size: 9px; }
.current-send-card strong { margin-top: 3px; font-size: 13px; }
.current-send-card p { margin: 10px 0 0; color: var(--greeting-copy); font-size: 11px; line-height: 1.65; }
.send-status-record { display: grid; gap: 11px; margin-top: 12px; border: 1px solid var(--greeting-line); border-radius: 14px; background: #fff; padding: 14px 16px; }
.send-status-record.failed { border-color: #efc8cc; background: #fffafb; }
.send-status-record.sent { border-color: #bde1dc; background: #f8fdfc; }
.send-status-record > header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.send-status-record > header span { color: var(--greeting-muted); font-size: 10px; font-weight: 800; }
.send-status-record > header strong { color: var(--greeting-blue-ink); font-size: 11px; }
.send-status-record.failed > header strong { color: var(--greeting-risk); }
.send-status-record dl { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 8px; margin: 0; }
.send-status-record dl div { border-radius: 9px; background: var(--greeting-canvas); padding: 8px 9px; }
.send-status-record dt { color: var(--greeting-muted); font-size: 9px; }
.send-status-record dd { margin: 3px 0 0; color: var(--greeting-copy); font-size: 10px; font-weight: 800; }
.send-status-record > p { margin: 0; border-left: 3px solid var(--greeting-risk); background: #fff3f4; color: #a93c47; padding: 8px 10px; font-size: 10px; line-height: 1.5; }
.send-status-record > small { color: var(--greeting-muted); font-size: 9px; line-height: 1.5; }
.simulation-boundary { border-left: 1px solid var(--greeting-line); background: #fbfdff; padding: 28px 22px; }
.boundary-mark { display: grid; width: 48px; height: 48px; place-items: center; border: 1px solid rgba(4,166,166,.25); border-radius: 15px; background: #edf9f7; color: #087d7d; font-size: 12px; font-weight: 900; }
.simulation-boundary h4 { margin: 17px 0 0; font-size: 14px; }
.simulation-boundary p { margin: 8px 0 0; color: var(--greeting-muted); font-size: 10px; line-height: 1.65; }
.simulation-boundary ul { display: grid; gap: 9px; margin: 19px 0 0; padding: 0; list-style: none; }
.simulation-boundary li { border-top: 1px solid var(--greeting-line); color: var(--greeting-copy); padding-top: 9px; font-size: 10px; }
.simulation-boundary.live .boundary-mark { border-color: rgba(8,105,216,.25); background: var(--greeting-blue-soft); color: var(--greeting-blue-ink); }

.greeting-footer { display: flex; min-height: 68px; align-items: center; justify-content: space-between; gap: 18px; border-top: 1px solid var(--greeting-line); background: #fff; padding: 10px 16px 10px 20px; }
.batch-limit-control { display: flex; align-items: center; gap: 8px; color: var(--greeting-copy); font-size: 10px; }
.batch-limit-control label { font-weight: 850; }
.batch-limit-control input { width: 52px; height: 34px; box-sizing: border-box; border: 1px solid var(--greeting-line-strong); border-radius: 8px; outline: 0; color: var(--greeting-ink); padding: 0 8px; font: 850 12px/1 var(--ui-font-utility, Consolas, monospace); }
.batch-limit-control input:focus { border-color: var(--greeting-blue); box-shadow: 0 0 0 3px var(--ui-focus, rgba(8,105,216,.12)); }
.batch-limit-control > span { color: var(--greeting-muted); }
.batch-limit-control > small { color: #a33b46; font-size: 10px; font-weight: 750; }
.batch-limit-control strong { color: var(--greeting-blue-ink); }
.local-boundary { gap: 8px; color: #087d7d; font-size: 10px; font-weight: 800; }
.local-boundary.live { color: var(--greeting-blue-ink); }
.footer-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
.primary-button,.secondary-button,.danger-button,.risk-confirm-button { min-height: 40px; border-radius: 10px; padding: 8px 14px; cursor: pointer; font-size: 11px; font-weight: 850; }
.primary-button { min-width: 148px; border: 1px solid var(--greeting-blue); background: var(--greeting-blue); color: #fff; box-shadow: 0 8px 20px rgba(8,105,216,.17); }
.secondary-button { border: 1px solid var(--greeting-line-strong); background: #fff; color: var(--greeting-copy); }
.danger-button { border: 1px solid #e4aab0; background: #fff3f4; color: #a93c47; }
.primary-button:hover:not(:disabled),.primary-button:focus-visible { background: var(--ui-accent-hover,#0058c7); outline: 0; box-shadow: 0 0 0 3px var(--ui-focus,rgba(8,105,216,.14)); }
.secondary-button:hover,.secondary-button:focus-visible { border-color: var(--greeting-blue); color: var(--greeting-blue-ink); outline: 0; }
.danger-button:hover,.danger-button:focus-visible { border-color: var(--greeting-risk); outline: 0; }
.primary-button:disabled,.risk-confirm-button:disabled { cursor: not-allowed; opacity: .45; }

.risk-layer { position: absolute; z-index: 3; inset: 0; display: grid; place-items: center; background: rgba(12,31,59,.42); padding: 24px; backdrop-filter: blur(4px); }
.risk-dialog { display: grid; width: min(520px,100%); grid-template-columns: 50px minmax(0,1fr); gap: 12px 15px; border: 1px solid #e4aab0; border-radius: 18px; background: #fff; padding: 22px; box-shadow: 0 24px 70px rgba(69,15,23,.24); }
.risk-mark { display: grid; width: 48px; height: 48px; grid-row: 1; place-items: center; border-radius: 15px; background: #fff0f1; color: var(--greeting-risk); font: 900 22px/1 var(--ui-font-display,sans-serif); }
.risk-dialog small { color: var(--greeting-risk); font-size: 9px; font-weight: 850; }
.risk-dialog h3 { margin: 4px 0 0; font: 850 20px/1.2 var(--ui-font-display,sans-serif); }
.risk-dialog > p,.risk-dialog > label,.risk-dialog > footer { grid-column: 1 / -1; }
.risk-dialog > p { margin: 5px 0 0; color: var(--greeting-copy); font-size: 11px; line-height: 1.75; }
.risk-dialog > label { display: flex; align-items: flex-start; gap: 8px; border: 1px solid #f0d2d5; border-radius: 10px; background: #fff8f8; color: #8f4249; padding: 10px; font-size: 10px; font-weight: 750; }
.risk-dialog > label input { margin-top: 1px; accent-color: var(--greeting-risk); }
.risk-dialog > footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 3px; }
.risk-confirm-button { border: 1px solid var(--greeting-risk); background: var(--greeting-risk); color: #fff; }
.risk-confirm-button:hover:not(:disabled),.risk-confirm-button:focus-visible { background: #ad3844; outline: 0; }

@keyframes greeting-dialog-in { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: none; } }
@keyframes track-pulse { from { transform: scale(.86); } to { transform: scale(1.08); } }

@media (max-width: 1120px) {
  .review-stage,.sending-stage { grid-template-columns: 270px minmax(480px,1fr); }
  .evidence-panel,.simulation-boundary { display: none; }
}

@media (max-width: 760px) {
  .greeting-backdrop { align-items: end; padding: 0; }
  .greeting-dialog { width: 100%; height: 96dvh; min-height: 0; border-radius: 20px 20px 0 0; }
  .greeting-header { min-height: 64px; padding: 11px 14px; }
  .greeting-title-block p,.resume-version { display: none; }
  .greeting-steps { padding: 0 12px; }
  .step-line { width: 24px; margin: 0 6px; }
  .review-stage,.sending-stage { grid-template-columns: 1fr; }
  .delivery-track { display: none; }
  .message-editor,.progress-board { padding: 20px 16px; }
  .greeting-footer { align-items: stretch; flex-direction: column; padding: 10px 12px; }
  .batch-limit-control,.footer-actions { width: 100%; }
  .footer-actions button { flex: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .greeting-dialog,.track-item.sending .track-node i { animation: none; }
  .progress-rail i,.message-paper { transition: none; }
}
</style>
