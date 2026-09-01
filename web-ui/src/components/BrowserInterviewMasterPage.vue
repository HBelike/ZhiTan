<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { BrowserAudioCapture, detectBrowserInterviewSupport } from '../browser-live-interview/capture.js'
import { openAnswerPictureInPicture, supportsAnswerPictureInPicture } from '../browser-live-interview/picture-in-picture.js'
import { createInterviewState, reduceInterviewEvent } from '../browser-live-interview/session-state.js'
import { LiveInterviewSocket } from '../browser-live-interview/socket.js'
import {
  advanceSetupProgress,
  answerPreviewLines,
  buildArchivePreview,
  estimateAsrCost,
  formatDuration,
  isNearScrollEnd,
  pickInitialAnswerModel
} from '../browser-live-interview/view.js'

const setupLoading = ref(true)
const setupProgress = ref(8)
const setupOptions = ref(null)
const selectedAnswerModelId = ref('')
const candidateEnabled = ref(false)
const consentAccepted = ref(false)
const support = ref({ supported: false, missing: ['正在检查浏览器能力'] })
const phase = ref('preparing')
const message = ref('')
const errorMessage = ref('')
const connectionState = ref('idle')
const state = ref(createInterviewState())
const elapsedSeconds = ref(0)
const allTranscripts = ref([])
const completedHistory = ref([])
const sessionIds = ref([])
const pipRoot = ref(null)
const pipWindow = ref(null)
const hiddenViewportEnabled = ref(false)
const hiddenViewportPending = ref(false)
const latencySamples = ref([])
const transcriptList = ref(null)
const followLatestTranscripts = ref(true)
const unseenTranscriptCount = ref(0)
const archiveDialogOpen = ref(false)
const archiveLoading = ref(false)
const archiveSaving = ref(false)
const archiveStatus = ref('idle')
const archiveError = ref('')
const archivePreviewFailed = ref(false)
const archivedExperienceId = ref('')
const archivePreview = ref(buildArchivePreview())
const archiveForm = ref({ companyName: '', roleName: '', interviewDate: '' })

let capture = null
let liveSocket = null
let timer = null
let setupProgressTimer = null
let startedAt = 0
let startGeneration = 0
const questionDetectedAt = new Map()
const answeredVersions = new Set()

const readyAnswerModels = computed(() => (
  setupOptions.value?.answer_models?.filter((item) => item.readiness === 'ready') || []
))
const asrReady = computed(() => setupOptions.value?.environment_asr?.readiness === 'ready')
const canStart = computed(() => (
  support.value.supported
  && asrReady.value
  && consentAccepted.value
  && !setupLoading.value
  && ['preparing', 'paused'].includes(phase.value)
))
const activeTrackCount = computed(() => (
  state.value.activeChannels.includes('candidate') || (phase.value === 'starting' && candidateEnabled.value) ? 2 : 1
))
const costEstimate = computed(() => estimateAsrCost(elapsedSeconds.value, activeTrackCount.value).toFixed(4))
const durationText = computed(() => formatDuration(elapsedSeconds.value))
const quickLines = computed(() => answerPreviewLines(state.value.answerText, 5))
const latestTranscripts = computed(() => allTranscripts.value.slice(-18))
const interviewerPartial = computed(() => state.value.partialTranscripts.interviewer || '')
const candidatePartial = computed(() => state.value.partialTranscripts.candidate || '')
const phaseLabel = computed(() => ({
  preparing: '准备采集',
  starting: '正在连接',
  active: connectionState.value === 'ready' ? '正在聆听' : '正在建立实时连接',
  paused: '采集已暂停',
  ended: '本场已结束'
}[phase.value] || '准备采集'))
const actionLabel = computed(() => phase.value === 'paused' ? '重新选择声音来源' : '开始面试')
const canOpenPip = computed(() => (
  phase.value === 'active'
  && connectionState.value === 'ready'
  && supportsAnswerPictureInPicture()
))
const hiddenViewportSupported = computed(() => supportsAnswerPictureInPicture())
const setupProgressLabel = computed(() => {
  if (setupProgress.value >= 100) return '模型与声音已准备完成'
  if (setupProgress.value >= 24) return '正在读取模型配置'
  return '正在检查浏览器能力'
})
const canSaveArchive = computed(() => (
  !archiveLoading.value
  && !archiveSaving.value
  && Boolean(archiveForm.value.companyName.trim())
  && Boolean(archiveForm.value.roleName.trim())
  && Boolean(archiveForm.value.interviewDate)
  && (archivePreview.value.questionCount > 0 || archivePreviewFailed.value)
))

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail || payload.message || `请求失败（${response.status}）`)
  return payload
}

async function loadSetupOptions() {
  setupLoading.value = true
  setupProgress.value = Math.max(24, setupProgress.value)
  errorMessage.value = ''
  stopSetupProgress()
  setupProgressTimer = window.setInterval(() => {
    setupProgress.value = advanceSetupProgress(setupProgress.value)
  }, 220)
  try {
    setupOptions.value = await requestJson('/api/career/live-interviews/setup-options')
    const preferred = new URLSearchParams(window.location.search).get('answer_model_profile_id') || ''
    selectedAnswerModelId.value = pickInitialAnswerModel(setupOptions.value.answer_models, preferred)
    setupProgress.value = 100
    await new Promise((resolve) => window.setTimeout(resolve, 180))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法读取模型配置'
  } finally {
    stopSetupProgress()
    setupLoading.value = false
  }
}

function stopSetupProgress() {
  if (setupProgressTimer) window.clearInterval(setupProgressTimer)
  setupProgressTimer = null
}

function handleServerEvent(event) {
  if (event.type === 'question.detected') questionDetectedAt.set(Number(event.question_version), performance.now())
  if (event.type === 'answer.delta' && Number(event.delta_index) === 1) {
    const detectedAt = questionDetectedAt.get(Number(event.question_version))
    if (detectedAt) latencySamples.value.push(Math.max(0, performance.now() - detectedAt))
  }
  if (event.type === 'transcript.final') {
    allTranscripts.value.push({
      sessionId: sessionIds.value.at(-1),
      channel: event.channel,
      text: event.text,
      sequence: event.sequence
    })
    allTranscripts.value = allTranscripts.value.slice(-100)
  }
  state.value = reduceInterviewEvent(state.value, event)
  if (event.type === 'answer.completed') {
    const key = `${sessionIds.value.at(-1)}:${event.question_version}:${event.attempt}`
    if (!answeredVersions.has(key)) {
      answeredVersions.add(key)
      completedHistory.value.push({
        key,
        question: state.value.currentQuestion,
        answer: state.value.answerText,
        intent: state.value.questionIntent
      })
      completedHistory.value = completedHistory.value.slice(-50)
    }
  }
  if (event.type === 'error') errorMessage.value = event.message || '实时处理出现错误'
}

function handleTranscriptScroll(event) {
  followLatestTranscripts.value = isNearScrollEnd(event.currentTarget)
  if (followLatestTranscripts.value) unseenTranscriptCount.value = 0
}

async function scrollTranscriptsToLatest({ force = false, smooth = false } = {}) {
  if (!force && !followLatestTranscripts.value) return
  await nextTick()
  const list = transcriptList.value
  if (!list) return
  const top = list.scrollHeight
  if (typeof list.scrollTo === 'function') list.scrollTo({ top, behavior: smooth ? 'smooth' : 'auto' })
  else list.scrollTop = top
  if (force) {
    followLatestTranscripts.value = true
    unseenTranscriptCount.value = 0
  }
}

function connectionFailureMessage(detail = {}) {
  if (detail.reason) return `实时连接已中断：${detail.reason}`
  if (detail.code) return `实时连接已中断（关闭代码 ${detail.code}）。`
  return '实时连接已中断，请检查本机服务后重试。'
}

function handleConnectionState(nextState, detail = {}) {
  connectionState.value = nextState
  if (nextState === 'backpressure') message.value = '网络发送稍慢，已跳过一小段音频以保持实时性。'
  if (nextState === 'disconnected' && detail.wasReady && phase.value === 'active') {
    void pauseCapture(connectionFailureMessage(detail))
  }
}

function isCurrentStart(generation) {
  return generation === startGeneration && phase.value === 'starting'
}

async function createAndConnectLiveSession(candidateAudioEnabled, generation) {
  let lastError = null
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    if (!isCurrentStart(generation)) throw new Error('实时连接启动已取消')
    const created = await requestJson('/api/career/live-interviews/sessions', {
      method: 'POST',
      body: JSON.stringify({
        asr_model_profile_id: null,
        answer_model_profile_id: selectedAnswerModelId.value || null,
        client_kind: 'browser',
        candidate_audio_enabled: candidateAudioEnabled
      })
    })
    const sessionId = created.session.id
    sessionIds.value.push(sessionId)
    if (!isCurrentStart(generation)) throw new Error('实时连接启动已取消')
    let socket = null
    socket = new LiveInterviewSocket({
      sessionId,
      onEvent: handleServerEvent,
      onStateChange: (nextState, detail) => {
        if (liveSocket !== socket) return
        handleConnectionState(nextState, detail)
      }
    })
    liveSocket = socket
    try {
      await socket.connect()
      if (liveSocket !== socket || !isCurrentStart(generation)) {
        throw new Error('实时连接已被新的连接替换')
      }
      errorMessage.value = ''
      return { retried: attempt > 1 }
    } catch (error) {
      lastError = error
      if (liveSocket === socket) liveSocket = null
      socket.close()
      if (!isCurrentStart(generation)) throw error
      if (attempt === 1) message.value = '首次实时连接未就绪，正在保留声音来源并自动重试…'
      if (attempt === 1) errorMessage.value = ''
    }
  }
  throw lastError || new Error('实时连接未能完成准备')
}

async function startInterview() {
  if (!canStart.value || phase.value === 'starting') return
  const generation = ++startGeneration
  phase.value = 'starting'
  errorMessage.value = ''
  message.value = ''
  state.value = createInterviewState()
  connectionState.value = 'connecting'
  capture = new BrowserAudioCapture()
  try {
    const captureResult = await capture.start({
      candidateEnabled: candidateEnabled.value,
      onFrame: ({ channel, sequence, pcm }) => liveSocket?.sendAudio(channel, sequence, pcm),
      onEnded: () => {
        if (generation === startGeneration) {
          void pauseCapture('电脑声音共享已停止，回答和历史已保留。')
        }
      }
    })
    if (!isCurrentStart(generation)) throw new Error('电脑声音共享已停止')
    candidateEnabled.value = captureResult.candidateEnabled
    const connection = await createAndConnectLiveSession(captureResult.candidateEnabled, generation)
    if (!isCurrentStart(generation)) throw new Error('实时连接启动已取消')
    phase.value = 'active'
    const notices = []
    if (captureResult.warning) notices.push(captureResult.warning)
    if (connection.retried) notices.push('首次实时连接中断，已自动恢复。')
    message.value = notices.join('；')
    startTimer()
  } catch (error) {
    if (generation !== startGeneration) return
    const socket = liveSocket
    liveSocket = null
    socket?.close()
    await capture?.stop()
    capture = null
    phase.value = sessionIds.value.length ? 'paused' : 'preparing'
    connectionState.value = 'idle'
    errorMessage.value = error instanceof Error ? error.message : '无法开始浏览器面试'
  }
}

function startTimer() {
  if (timer) return
  startedAt = performance.now() - elapsedSeconds.value * 1000
  timer = window.setInterval(() => {
    elapsedSeconds.value = Math.floor((performance.now() - startedAt) / 1000)
    if (elapsedSeconds.value === 115 * 60) message.value = '本场将在 5 分钟后自动结束，请留意剩余时间。'
    if (elapsedSeconds.value >= 120 * 60) void endInterview('已达到单场 120 分钟上限。')
  }, 1000)
}

async function pauseCapture(reason) {
  if (!['active', 'starting'].includes(phase.value)) return
  startGeneration += 1
  phase.value = 'paused'
  message.value = reason
  const socket = liveSocket
  liveSocket = null
  socket?.end()
  const currentCapture = capture
  capture = null
  await currentCapture?.stop()
}

async function endInterview(reason = '本场面试已结束。') {
  if (phase.value === 'ended') return
  startGeneration += 1
  phase.value = 'ended'
  message.value = reason
  const socket = liveSocket
  liveSocket = null
  socket?.end()
  const currentCapture = capture
  capture = null
  await currentCapture?.stop()
  if (timer) window.clearInterval(timer)
  timer = null
  hiddenViewportEnabled.value = false
  closePip()
  await openArchiveDialog()
}

async function openArchiveDialog() {
  archiveDialogOpen.value = true
  archiveLoading.value = true
  archiveSaving.value = false
  archiveStatus.value = 'idle'
  archiveError.value = ''
  archivePreviewFailed.value = false
  archivedExperienceId.value = ''
  archivePreview.value = buildArchivePreview({}, completedHistory.value)
  archiveForm.value = {
    companyName: '',
    roleName: '',
    interviewDate: archivePreview.value.interviewDate
  }
  if (!sessionIds.value.length) {
    archiveLoading.value = false
    archiveError.value = '本场没有可归档的实时面试会话。'
    return
  }
  try {
    const payload = await requestJson('/api/career/live-interviews/archive/preview', {
      method: 'POST',
      body: JSON.stringify({ session_ids: sessionIds.value })
    })
    archivePreview.value = buildArchivePreview(payload, completedHistory.value)
    archiveForm.value.interviewDate = archivePreview.value.interviewDate
  } catch (error) {
    archivePreviewFailed.value = true
    archiveError.value = `${error instanceof Error ? error.message : '无法读取完整问题预览'}；保存时会重新读取本场完整会话。`
  } finally {
    archiveLoading.value = false
  }
}

function discardArchive() {
  if (archiveSaving.value) return
  archiveDialogOpen.value = false
  archiveStatus.value = 'discarded'
  archiveError.value = ''
}

async function saveArchive() {
  if (!canSaveArchive.value) {
    if (!archiveForm.value.companyName.trim() || !archiveForm.value.roleName.trim() || !archiveForm.value.interviewDate) {
      archiveError.value = '请填写公司名称、面试职位和面试日期。'
    }
    return
  }
  archiveSaving.value = true
  archiveError.value = ''
  try {
    const payload = await requestJson('/api/career/live-interviews/archive', {
      method: 'POST',
      body: JSON.stringify({
        session_ids: sessionIds.value,
        company_name: archiveForm.value.companyName.trim(),
        role_name: archiveForm.value.roleName.trim(),
        interview_date: archiveForm.value.interviewDate
      })
    })
    archiveStatus.value = 'saved'
    archivedExperienceId.value = payload.experience?.id || ''
    archivePreview.value = buildArchivePreview(payload, archivePreview.value.questions)
  } catch (error) {
    archiveError.value = error instanceof Error ? error.message : '保存面经失败，请稍后重试。'
  } finally {
    archiveSaving.value = false
  }
}

function viewArchivedExperience() {
  if (!archivedExperienceId.value) return
  window.location.assign(`/interviews?experience_id=${encodeURIComponent(archivedExperienceId.value)}`)
}

function answerNow() {
  const latest = [...allTranscripts.value].reverse().find((item) => item.channel === 'interviewer')
  if (!latest) {
    errorMessage.value = '尚未收到可用于回答的面试官语音。'
    return
  }
  errorMessage.value = ''
  liveSocket?.requestAnswer('manual', latest.text)
}

function regenerateAnswer() {
  if (!state.value.currentQuestion) return
  errorMessage.value = ''
  liveSocket?.requestAnswer('regenerate')
}

async function ensurePipWindow() {
  if (pipWindow.value && !pipWindow.value.closed) return true
  try {
    const result = await openAnswerPictureInPicture({ onClosed: handlePipClosed })
    pipWindow.value = result.window
    pipRoot.value = result.root
    await nextTick()
    return true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法打开置顶小窗'
    return false
  }
}

async function openPip() {
  if (!canOpenPip.value) {
    message.value = '当前 Chrome 不支持置顶小窗，请继续使用本独立窗口。'
    return
  }
  await ensurePipWindow()
}

async function toggleHiddenViewport(event) {
  const shouldEnable = event?.target ? Boolean(event.target.checked) : !hiddenViewportEnabled.value
  if (!shouldEnable) {
    hiddenViewportEnabled.value = false
    closePip()
    message.value = '隐藏视口V1 已关闭，回答已恢复到当前页面。'
    return
  }
  if (!hiddenViewportSupported.value) {
    hiddenViewportEnabled.value = false
    errorMessage.value = '当前浏览器不支持隐藏视口V1，请使用最新版 Chrome 或 Edge。'
    return
  }
  if (phase.value !== 'active' || connectionState.value !== 'ready') {
    hiddenViewportEnabled.value = false
    errorMessage.value = '实时连接尚未准备完成，请等待状态变为“正在聆听”后再开启隐藏视口V1。'
    return
  }
  hiddenViewportPending.value = true
  errorMessage.value = ''
  const opened = await ensurePipWindow()
  hiddenViewportPending.value = false
  if (!opened) {
    hiddenViewportEnabled.value = false
    return
  }
  hiddenViewportEnabled.value = true
  message.value = '隐藏视口V1 已开启；仅分享指定标签页或窗口时生效。'
}

function handlePipClosed() {
  pipRoot.value = null
  pipWindow.value = null
  if (!hiddenViewportEnabled.value) return
  hiddenViewportEnabled.value = false
  message.value = '回答小窗已关闭，隐藏视口V1 已同步关闭。'
}

function closePip(closeWindow = true) {
  const activeWindow = pipWindow.value
  pipRoot.value = null
  pipWindow.value = null
  if (closeWindow && activeWindow && !activeWindow.closed) activeWindow.close()
}

function goBack() {
  window.location.assign('/career')
}

watch(() => allTranscripts.value.length, (nextLength, previousLength) => {
  if (nextLength > previousLength && !followLatestTranscripts.value) {
    unseenTranscriptCount.value += nextLength - previousLength
  }
  void scrollTranscriptsToLatest()
})

watch([interviewerPartial, candidatePartial], () => {
  void scrollTranscriptsToLatest()
})

onMounted(() => {
  try {
    support.value = detectBrowserInterviewSupport()
  } catch {
    support.value = { supported: false, missing: ['无法检查 Chrome 音频能力，请刷新页面重试'] }
  }
  setupProgress.value = 24
  void loadSetupOptions()
})

onBeforeUnmount(() => {
  startGeneration += 1
  stopSetupProgress()
  if (timer) window.clearInterval(timer)
  timer = null
  liveSocket?.close()
  void capture?.stop()
  closePip()
})
</script>

<template>
  <main class="interview-master-shell">
    <header class="interview-master-header">
      <button class="back-button" type="button" @click="goBack">← 返回求职助手</button>
      <div class="brand-mark" aria-label="面试大师">
        <span class="brand-wave"><i></i><i></i><i></i><i></i></span>
        <strong>面试大师</strong>
      </div>
      <div class="session-metrics">
        <span>{{ durationText }}</span>
        <span>预计 ¥{{ costEstimate }}</span>
      </div>
    </header>

    <section v-if="phase === 'preparing' && !sessionIds.length" class="preparation-layout">
      <article class="preparation-thesis">
        <div class="pulse-orbit" aria-hidden="true"><span></span><span></span><span></span></div>
        <p class="section-label">浏览器电脑声音实时辅助</p>
        <h1>听清问题，<br />把回答组织成能开口的话。</h1>
        <p>浏览器获得电脑声音授权后，Qwen 负责中英混合转写，当前文本模型把面试官的问题实时整理为中文回答。腾讯会议、飞书、QQ、微信或浏览器通话都走同一条电脑音频轨，不需要绑定平台。</p>
        <div class="capture-boundary">
          <strong>本场只处理你主动授权的声音</strong>
          <span>不保存原始音频、屏幕画面、转写半句或你的麦克风文本。</span>
        </div>
      </article>

      <article class="preparation-panel">
        <div class="preparation-heading">
          <div>
            <p class="section-label">开始准备</p>
            <h2>检查模型与声音</h2>
          </div>
          <label
            class="hidden-viewport-control"
            :class="{ active: hiddenViewportEnabled, unsupported: !hiddenViewportSupported, locked: hiddenViewportSupported && connectionState !== 'ready' }"
          >
            <span class="hidden-viewport-copy">
              <strong>隐藏视口V1</strong>
              <small v-if="hiddenViewportSupported">
                {{ hiddenViewportEnabled ? '独立小窗已开启；仅分享指定标签页或窗口时生效。' : '面试开始且实时连接就绪后可开启；分享整个屏幕时仍会显示。' }}
              </small>
              <small v-else>当前浏览器不支持，请使用最新版 Chrome 或 Edge。</small>
            </span>
            <span class="hidden-viewport-switch" aria-hidden="true"><i></i></span>
            <input
              type="checkbox"
              :checked="hiddenViewportEnabled"
              :disabled="hiddenViewportPending || !hiddenViewportSupported || connectionState !== 'ready'"
              role="switch"
              aria-label="隐藏视口V1"
              @change="toggleHiddenViewport"
            />
          </label>
        </div>

        <div v-if="setupLoading" class="setup-loading" role="status" aria-live="polite">
          <div class="setup-loading-copy">
            <span>{{ setupProgressLabel }}</span>
            <strong>{{ Math.round(setupProgress) }}%</strong>
          </div>
          <div
            class="setup-progress-track"
            role="progressbar"
            aria-label="面试准备进度"
            aria-valuemin="0"
            aria-valuemax="100"
            :aria-valuenow="Math.round(setupProgress)"
          >
            <span :style="{ width: `${setupProgress}%` }"></span>
          </div>
          <div class="setup-progress-stages" aria-hidden="true">
            <span class="complete">检查浏览器</span>
            <span :class="{ complete: setupProgress >= 24 }">读取模型</span>
            <span :class="{ complete: setupProgress >= 100 }">完成准备</span>
          </div>
        </div>
        <template v-else>
          <label class="field-group">
            <span>实时转写</span>
            <strong :class="{ blocked: !asrReady }">{{ setupOptions?.environment_asr?.display_name || '未配置' }}</strong>
            <small>{{ setupOptions?.environment_asr?.model_id || setupOptions?.environment_asr?.blocked_reason }}</small>
          </label>

          <label class="field-group">
            <span>回答模型</span>
            <select v-model="selectedAnswerModelId">
              <option value="">免费额度优先</option>
              <option v-for="model in readyAnswerModels" :key="model.id" :value="model.id">
                {{ model.display_name }} · {{ model.model_id }}
              </option>
            </select>
          </label>

          <label class="toggle-row">
            <span><strong>同时理解我的回答</strong><small>默认关闭；仅用于理解后续追问，不触发答案、不保存文字。</small></span>
            <input v-model="candidateEnabled" type="checkbox" />
          </label>

          <div class="browser-check" :class="{ ok: support.supported }">
            <span class="status-dot"></span>
            <div>
              <strong>{{ support.supported ? '桌面版 Chrome、Edge 或 Firefox 能力正常' : '当前环境暂不可开始' }}</strong>
              <small>{{ support.supported ? '点击开始后请选择带声音的共享来源；Chrome、Edge 可优先选择“整个屏幕”并开启系统音频，Firefox 请使用授权窗口实际提供的音频来源。' : support.missing.join('；') }}</small>
            </div>
          </div>

          <label class="consent-row">
            <input v-model="consentAccepted" type="checkbox" />
            <span>我已了解采集范围，并确认当前场景允许使用实时辅助。</span>
          </label>
        </template>

        <p v-if="errorMessage" class="inline-error">{{ errorMessage }}</p>
        <button class="primary-start" type="button" :disabled="!canStart" @click="startInterview">
          开始面试
        </button>
      </article>
    </section>

    <section v-else-if="hiddenViewportEnabled" class="hidden-viewport-stage">
      <article class="hidden-viewport-card">
        <span class="hidden-viewport-emblem" aria-hidden="true"><i></i></span>
        <p class="section-label">浏览器隔离模式</p>
        <h1>隐藏视口V1 已开启</h1>
        <p>回答与问题已移至独立小窗，当前页面不再展示面试内容。</p>
        <div class="hidden-viewport-warning">
          仅分享指定标签页或窗口时生效；分享整个屏幕时，小窗仍会出现在共享画面中。
        </div>
        <div class="hidden-viewport-actions">
          <button type="button" @click="toggleHiddenViewport">关闭隐藏视口V1</button>
          <button v-if="phase === 'paused'" type="button" :disabled="!canStart" @click="startInterview">{{ actionLabel }}</button>
          <button v-else-if="phase !== 'ended'" type="button" @click="pauseCapture('已暂停采集，结果仍保留在回答小窗。')">暂停采集</button>
          <button v-if="phase !== 'ended'" class="danger" type="button" @click="endInterview()">结束面试</button>
        </div>
      </article>
    </section>

    <section v-else class="live-layout">
      <aside class="live-rail">
        <div class="live-status" :class="phase">
          <div class="question-pulse" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
          <div><strong>{{ phaseLabel }}</strong><small>{{ connectionState }}</small></div>
        </div>

        <section class="rail-section">
          <header>
            <h2>实时转写</h2>
            <div class="transcript-tools">
              <button v-if="!followLatestTranscripts" type="button" @click="scrollTranscriptsToLatest({ force: true, smooth: true })">
                {{ unseenTranscriptCount ? `${unseenTranscriptCount} 条新消息` : '回到最新' }} ↓
              </button>
              <span>{{ allTranscripts.length }}</span>
            </div>
          </header>
          <div ref="transcriptList" class="transcript-list" @scroll.passive="handleTranscriptScroll">
            <article v-for="(item, index) in latestTranscripts" :key="`${item.sessionId}-${item.channel}-${item.sequence}-${index}`" :class="item.channel">
              <span>{{ item.channel === 'interviewer' ? '面试官' : '我' }}</span>
              <p>{{ item.text }}</p>
            </article>
            <article v-if="interviewerPartial" class="interviewer partial"><span>识别中</span><p>{{ interviewerPartial }}</p></article>
            <article v-if="candidatePartial" class="candidate partial"><span>我</span><p>{{ candidatePartial }}</p></article>
            <p v-if="!latestTranscripts.length && !interviewerPartial" class="empty-copy">共享开始后，面试官的完整话语会出现在这里。</p>
          </div>
        </section>

        <div class="rail-actions">
          <button v-if="phase === 'paused'" type="button" :disabled="!canStart" @click="startInterview">{{ actionLabel }}</button>
          <button v-else-if="phase !== 'ended'" type="button" @click="pauseCapture('已暂停采集，结果仍保留在当前窗口。')">暂停采集</button>
          <button v-if="phase !== 'ended'" class="danger" type="button" @click="endInterview()">结束面试</button>
          <button v-else type="button" @click="goBack">返回求职助手</button>
        </div>
      </aside>

      <section class="answer-workspace">
        <div v-if="message" class="notice-strip">{{ message }}</div>
        <div v-if="errorMessage" class="notice-strip error">{{ errorMessage }}</div>

        <article class="current-question-card">
          <div class="question-meta"><span>当前问题</span><em>{{ state.questionIntent || '等待识别' }}</em></div>
          <h1>{{ state.currentQuestion || '面试官的完整问题会显示在这里' }}</h1>
          <div class="question-actions">
            <button type="button" :disabled="phase !== 'active'" @click="answerNow">立即回答</button>
            <button type="button" :disabled="!state.currentQuestion || phase !== 'active'" @click="regenerateAnswer">重新生成</button>
            <button type="button" :disabled="!canOpenPip" @click="openPip">打开置顶小窗</button>
            <button type="button" :disabled="!canOpenPip" @click="toggleHiddenViewport">开启隐藏视口V1</button>
          </div>
        </article>

        <div class="answer-grid">
          <article class="quick-answer-card">
            <header><span>先开口</span><small>约 30 秒提纲</small></header>
            <ol v-if="quickLines.length">
              <li v-for="line in quickLines" :key="line">{{ line }}</li>
            </ol>
            <p v-else class="empty-copy">识别到完整问题后，这里优先给出可以直接开口的结论和要点。</p>
          </article>

          <article class="full-answer-card">
            <header>
              <div><span>完整回答</span><small>{{ state.answerStatus === 'generating' ? '正在流式生成' : '统一中文' }}</small></div>
              <i v-if="state.answerStatus === 'generating'" class="typing-dot"></i>
            </header>
            <div class="answer-text">{{ state.answerText || '等待面试官提出问题…' }}</div>
          </article>
        </div>

        <section class="history-section">
          <header><h2>本场问答</h2><span>{{ completedHistory.length }} 题</span></header>
          <div v-if="completedHistory.length" class="history-list">
            <details v-for="(item, index) in [...completedHistory].reverse()" :key="item.key" :open="index === 0">
              <summary><span>Q{{ completedHistory.length - index }}</span>{{ item.question }}</summary>
              <p>{{ item.answer }}</p>
            </details>
          </div>
          <p v-else class="empty-copy">完整问题与最终回答会保留在这里；你的麦克风转写不会进入历史。</p>
        </section>
      </section>
    </section>

    <Teleport to="body">
      <div
        v-if="archiveDialogOpen"
        class="archive-backdrop"
        role="presentation"
        @click.self="discardArchive"
      >
        <section class="archive-dialog" role="dialog" aria-modal="true" aria-labelledby="archive-dialog-title">
          <header class="archive-dialog-header">
            <div>
              <span class="archive-mark" aria-hidden="true">问</span>
              <div>
                <h2 id="archive-dialog-title">保存本次面经？</h2>
                <p v-if="archiveLoading">正在读取本场完整问题…</p>
                <p v-else-if="archivePreview.questionCount">本场识别到 {{ archivePreview.questionCount }} 个问题，保存后可在面经库检索。</p>
                <p v-else>本场未识别到可归档的问题。</p>
              </div>
            </div>
            <button type="button" aria-label="本次不保存并关闭" :disabled="archiveSaving" @click="discardArchive">×</button>
          </header>

          <div v-if="archiveStatus === 'saved'" class="archive-success" role="status">
            <span aria-hidden="true">✓</span>
            <h3>已保存到面经库</h3>
            <p>{{ archivePreview.questionCount }} 个问题已完成归并和检索索引。</p>
            <div>
              <button type="button" class="archive-secondary" @click="goBack">返回求职助手</button>
              <button type="button" class="archive-primary" @click="viewArchivedExperience">查看面经</button>
            </div>
          </div>

          <form v-else class="archive-form" @submit.prevent="saveArchive">
            <div class="archive-fields">
              <label>
                <span>公司名称 <em>*</em></span>
                <input v-model="archiveForm.companyName" type="text" maxlength="120" autocomplete="organization" placeholder="请输入公司名称" />
              </label>
              <label>
                <span>面试职位 <em>*</em></span>
                <input v-model="archiveForm.roleName" type="text" maxlength="160" placeholder="请输入职位名称" />
              </label>
              <label class="archive-date-field">
                <span>面试日期 <em>*</em></span>
                <input v-model="archiveForm.interviewDate" type="date" />
              </label>
            </div>

            <section class="archive-question-preview" aria-live="polite">
              <header>
                <strong>问题预览</strong>
                <span>共 {{ archivePreview.questionCount }} 题</span>
              </header>
              <ol v-if="archivePreview.questions.length">
                <li v-for="question in archivePreview.questions" :key="question">{{ question }}</li>
              </ol>
              <p v-else>{{ archiveLoading ? '正在整理问题清单…' : '没有可展示的问题。' }}</p>
              <small v-if="archivePreview.remainingCount">其余 {{ archivePreview.remainingCount }} 个问题将在保存后写入面经。</small>
            </section>

            <p v-if="archiveError" class="archive-error" role="alert">{{ archiveError }}</p>
            <footer>
              <button type="button" class="archive-secondary" :disabled="archiveSaving" @click="discardArchive">本次不保存</button>
              <button type="submit" class="archive-primary" :disabled="!canSaveArchive">
                {{ archiveSaving ? '正在整理并建立索引…' : '保存到面经库' }}
              </button>
            </footer>
          </form>
        </section>
      </div>
    </Teleport>

    <Teleport v-if="pipRoot" :to="pipRoot">
      <main class="answer-pip-card">
        <header><span class="status-dot"></span><strong>面试大师</strong><small>{{ phaseLabel }}</small></header>
        <section v-if="phase === 'preparing' || phase === 'starting'"><span>隐藏视口V1</span><h1>等待开始面试</h1></section>
        <section><span>当前问题</span><h1>{{ state.currentQuestion || '等待问题' }}</h1></section>
        <section class="pip-answer"><span>回答建议</span><p>{{ state.answerText || '正在聆听面试官…' }}</p></section>
      </main>
    </Teleport>
  </main>
</template>

<style scoped>
:global(body) { margin: 0; background: #edf3fb; }
:global(*) { box-sizing: border-box; }
.interview-master-shell { min-height: 100vh; color: #10213e; background: radial-gradient(circle at 18% 14%, rgba(43, 125, 255, .12), transparent 28%), #edf3fb; font-family: "Microsoft YaHei", "PingFang SC", sans-serif; }
.interview-master-header { height: 68px; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; padding: 0 28px; background: rgba(255,255,255,.9); border-bottom: 1px solid #d9e3f2; backdrop-filter: blur(14px); }
.back-button { justify-self: start; border: 0; background: transparent; color: #53657e; cursor: pointer; font-weight: 600; }
.brand-mark { display: flex; align-items: center; gap: 10px; font-size: 18px; }
.brand-wave { height: 22px; display: flex; align-items: end; gap: 3px; }
.brand-wave i { width: 3px; border-radius: 3px; background: #1671e8; animation: wave 1.3s ease-in-out infinite; }
.brand-wave i:nth-child(1) { height: 8px; }.brand-wave i:nth-child(2) { height: 18px; animation-delay: -.2s; }.brand-wave i:nth-child(3) { height: 13px; animation-delay: -.4s; }.brand-wave i:nth-child(4) { height: 21px; animation-delay: -.6s; }
.session-metrics { justify-self: end; display: flex; gap: 8px; }.session-metrics span { padding: 7px 11px; border-radius: 999px; background: #f0f5fc; color: #51637c; font: 600 12px ui-monospace, Consolas, monospace; }
.preparation-layout { min-height: calc(100vh - 68px); display: grid; grid-template-columns: minmax(420px, 1fr) minmax(520px, 1.15fr); }
.preparation-thesis { position: relative; overflow: hidden; padding: clamp(64px, 8vw, 126px); display: flex; flex-direction: column; justify-content: center; background: #0b1d3d; color: white; }
.preparation-thesis h1 { position: relative; margin: 16px 0 28px; max-width: 720px; font-family: "Microsoft YaHei UI", "PingFang SC", sans-serif; font-size: clamp(42px, 5.3vw, 82px); line-height: 1.12; letter-spacing: -.055em; }
.preparation-thesis > p:not(.section-label) { max-width: 650px; color: #b8cae7; font-size: 17px; line-height: 1.9; }
.section-label { color: #72aef7; font-size: 13px; font-weight: 800; letter-spacing: .12em; }
.capture-boundary { margin-top: 64px; padding-top: 24px; max-width: 610px; display: grid; gap: 8px; border-top: 1px solid rgba(255,255,255,.18); }.capture-boundary span { color: #92a9cb; font-size: 14px; }
.pulse-orbit { position: absolute; right: -110px; bottom: -120px; width: 440px; height: 440px; border: 1px solid rgba(59, 147, 255, .2); border-radius: 50%; }.pulse-orbit span { position: absolute; inset: 60px; border: 1px solid rgba(59,147,255,.15); border-radius: inherit; }.pulse-orbit span:nth-child(2) { inset: 130px; }.pulse-orbit span:nth-child(3) { inset: 205px; background: #1671e8; box-shadow: 0 0 80px rgba(22,113,232,.8); }
.preparation-panel { padding: clamp(52px, 7vw, 110px); display: flex; flex-direction: column; justify-content: center; gap: 20px; background: #f7faff; }.preparation-panel h2 { margin: 6px 0 12px; font-size: 36px; letter-spacing: -.03em; }
.preparation-heading { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(280px, 330px); align-items: start; gap: 22px; }
.hidden-viewport-control { position: relative; min-height: 78px; padding: 14px 14px 14px 16px; display: flex; align-items: center; justify-content: space-between; gap: 14px; border: 1px solid #cbd9eb; border-radius: 15px; background: rgba(255,255,255,.86); box-shadow: 0 8px 22px rgba(34,67,107,.06); cursor: pointer; transition: border-color .18s ease, background .18s ease, box-shadow .18s ease; }.hidden-viewport-control:hover { border-color: #90b8ea; }.hidden-viewport-control:focus-within { border-color: #176fe5; box-shadow: 0 0 0 4px rgba(23,111,229,.12); }.hidden-viewport-control.active { border-color: #67b49a; background: #edf9f4; box-shadow: 0 10px 24px rgba(31,132,96,.1); }.hidden-viewport-control.unsupported, .hidden-viewport-control.locked { cursor: not-allowed; opacity: .68; }.hidden-viewport-copy { min-width: 0; display: grid; gap: 5px; }.hidden-viewport-copy strong { color: #17365f; font-size: 14px; }.hidden-viewport-copy small { color: #6c7f98; font-size: 11px; line-height: 1.45; }.hidden-viewport-control.active .hidden-viewport-copy strong { color: #176449; }.hidden-viewport-switch { position: relative; width: 46px; height: 26px; flex: 0 0 auto; border-radius: 999px; background: #c6d2e2; box-shadow: inset 0 1px 2px rgba(24,50,84,.16); transition: background .18s ease; }.hidden-viewport-switch i { position: absolute; top: 4px; left: 4px; width: 18px; height: 18px; border-radius: 50%; background: white; box-shadow: 0 2px 6px rgba(20,44,75,.25); transition: transform .18s ease; }.hidden-viewport-control.active .hidden-viewport-switch { background: #29966e; }.hidden-viewport-control.active .hidden-viewport-switch i { transform: translateX(20px); }.hidden-viewport-control input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }.hidden-viewport-control input:disabled { cursor: not-allowed; }
.setup-loading { padding: 20px; display: grid; gap: 12px; border: 1px solid #d7e2f1; border-radius: 14px; background: rgba(255,255,255,.82); box-shadow: 0 9px 24px rgba(34,67,107,.05); }
.setup-loading-copy { display: flex; align-items: center; justify-content: space-between; gap: 20px; color: #53657e; font-size: 14px; }.setup-loading-copy strong { color: #176fe5; font: 800 13px ui-monospace, Consolas, monospace; }
.setup-progress-track { position: relative; height: 9px; overflow: hidden; border-radius: 999px; background: #dfe8f4; box-shadow: inset 0 1px 2px rgba(20,51,91,.08); }.setup-progress-track > span { position: absolute; inset: 0 auto 0 0; border-radius: inherit; background: linear-gradient(90deg, #176fe5, #3f96ff); box-shadow: 0 0 16px rgba(23,111,229,.35); transition: width .24s ease-out; }.setup-progress-track > span::after { content: ''; position: absolute; inset: 0; background: linear-gradient(105deg, transparent 30%, rgba(255,255,255,.55) 50%, transparent 70%); transform: translateX(-100%); animation: progress-glint 1.35s ease-in-out infinite; }
.setup-progress-stages { display: flex; justify-content: space-between; color: #9aabc0; font-size: 11px; font-weight: 700; }.setup-progress-stages span { transition: color .2s ease; }.setup-progress-stages span.complete { color: #176fe5; }
.field-group { padding: 18px 20px; display: grid; gap: 7px; border: 1px solid #d7e2f1; border-radius: 14px; background: white; }.field-group > span { color: #6a7d97; font-size: 12px; font-weight: 700; }.field-group strong { font-size: 16px; }.field-group strong.blocked { color: #b74343; }.field-group small { color: #7c8da4; }.field-group select { width: 100%; padding: 5px 0; border: 0; outline: 0; background: white; color: #10213e; font: inherit; font-weight: 700; }
.toggle-row, .consent-row { display: flex; align-items: center; justify-content: space-between; gap: 18px; }.toggle-row { padding: 18px 20px; border: 1px solid #d7e2f1; border-radius: 14px; background: white; }.toggle-row span { display: grid; gap: 4px; }.toggle-row small { color: #7c8da4; }.toggle-row input { width: 44px; height: 24px; accent-color: #1671e8; }.consent-row { justify-content: flex-start; color: #53657e; font-size: 13px; }.consent-row input { width: 18px; height: 18px; accent-color: #1671e8; }
.browser-check { padding: 16px 18px; display: flex; gap: 12px; border-radius: 14px; background: #fff2f1; color: #9c3838; }.browser-check.ok { background: #eaf8f3; color: #176449; }.browser-check > div { display: grid; gap: 4px; }.browser-check small { line-height: 1.5; }.status-dot { width: 9px; height: 9px; margin-top: 5px; flex: 0 0 auto; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 5px currentColor; opacity: .6; }
.primary-start { min-height: 58px; border: 0; border-radius: 14px; background: #176fe5; color: white; font-size: 17px; font-weight: 800; cursor: pointer; box-shadow: 0 14px 28px rgba(23,111,229,.22); }.primary-start:disabled { background: #aabbd2; box-shadow: none; cursor: not-allowed; }.inline-error { margin: 0; padding: 12px 14px; border-left: 3px solid #d34a4a; background: #fff1f1; color: #a63838; }
.hidden-viewport-stage { min-height: calc(100vh - 68px); padding: 40px; display: grid; place-items: center; background: radial-gradient(circle at 50% 38%, rgba(38,139,105,.12), transparent 28%), #edf3fb; }.hidden-viewport-card { width: min(620px, 100%); padding: 42px; display: grid; justify-items: center; border: 1px solid #cbdbea; border-radius: 24px; background: rgba(255,255,255,.92); box-shadow: 0 28px 80px rgba(20,52,91,.12); text-align: center; }.hidden-viewport-emblem { position: relative; width: 72px; height: 52px; margin-bottom: 20px; border: 3px solid #29966e; border-radius: 15px; background: #e7f7f1; }.hidden-viewport-emblem::before { content: ''; position: absolute; left: 50%; bottom: -12px; width: 24px; height: 3px; border-radius: 3px; background: #29966e; transform: translateX(-50%); }.hidden-viewport-emblem::after { content: ''; position: absolute; left: 50%; bottom: -7px; width: 3px; height: 8px; background: #29966e; transform: translateX(-50%); }.hidden-viewport-emblem i { position: absolute; inset: 11px 16px; border-radius: 50%; border: 3px solid #29966e; }.hidden-viewport-emblem i::after { content: ''; position: absolute; left: -5px; top: 50%; width: 40px; height: 3px; border-radius: 3px; background: #29966e; transform: rotate(-32deg); }.hidden-viewport-card .section-label { margin: 0; color: #278060; }.hidden-viewport-card h1 { margin: 10px 0 12px; font-size: clamp(30px, 4vw, 46px); letter-spacing: -.045em; }.hidden-viewport-card > p:not(.section-label) { margin: 0; color: #586d87; font-size: 16px; line-height: 1.7; }.hidden-viewport-warning { margin-top: 24px; padding: 13px 16px; border-left: 3px solid #e3a02c; border-radius: 3px 11px 11px 3px; background: #fff7e8; color: #825a18; font-size: 13px; line-height: 1.55; text-align: left; }.hidden-viewport-actions { margin-top: 28px; display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; }.hidden-viewport-actions button { min-height: 42px; padding: 0 17px; border: 1px solid #ccd9e9; border-radius: 10px; background: white; color: #29405f; font: inherit; font-size: 13px; font-weight: 800; cursor: pointer; }.hidden-viewport-actions button:first-child { border-color: #176fe5; background: #176fe5; color: white; }.hidden-viewport-actions button.danger { border-color: #efc3c3; color: #a23c3c; }.hidden-viewport-actions button:disabled { opacity: .5; cursor: not-allowed; }
.live-layout { height: calc(100vh - 68px); min-height: 0; overflow: hidden; display: grid; grid-template-columns: 330px 1fr; }.live-rail { height: 100%; min-height: 0; overflow: hidden; padding: 22px; display: flex; flex-direction: column; gap: 22px; background: #0b1d3d; color: white; }
.live-status { padding: 16px; display: flex; align-items: center; gap: 14px; border: 1px solid rgba(255,255,255,.13); border-radius: 16px; background: rgba(255,255,255,.05); }.live-status > div:last-child { display: grid; gap: 3px; }.live-status small { color: #8fa7ca; }.question-pulse { height: 34px; display: flex; align-items: center; gap: 3px; }.question-pulse span { width: 3px; height: 10px; border-radius: 3px; background: #52a4ff; }.live-status.active .question-pulse span { animation: wave 1.05s ease-in-out infinite; }.question-pulse span:nth-child(2) { height: 25px; animation-delay: -.2s; }.question-pulse span:nth-child(3) { height: 17px; animation-delay: -.4s; }.question-pulse span:nth-child(4) { height: 30px; animation-delay: -.6s; }
.rail-section { flex: 1; min-height: 0; display: flex; flex-direction: column; }.rail-section > header, .history-section > header { display: flex; align-items: center; justify-content: space-between; }.rail-section h2, .history-section h2 { margin: 0; font-size: 14px; }.transcript-tools { display: flex; align-items: center; gap: 8px; }.rail-section header span { color: #6f8ab2; font: 700 12px ui-monospace, monospace; }.transcript-tools button { padding: 4px 8px; border: 1px solid rgba(110,174,254,.28); border-radius: 999px; background: rgba(28,123,242,.15); color: #8cc1ff; font-size: 11px; cursor: pointer; }.transcript-list { flex: 1; min-height: 0; margin-top: 12px; padding-right: 8px; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable; scrollbar-width: thin; scrollbar-color: #47698f rgba(255,255,255,.06); display: flex; flex-direction: column; gap: 10px; }.transcript-list::-webkit-scrollbar { width: 7px; }.transcript-list::-webkit-scrollbar-track { border-radius: 999px; background: rgba(255,255,255,.05); }.transcript-list::-webkit-scrollbar-thumb { border-radius: 999px; background: #47698f; }.transcript-list article { padding: 12px; border-radius: 12px; background: rgba(255,255,255,.06); }.transcript-list article.candidate { background: rgba(28,123,242,.14); }.transcript-list article.partial { opacity: .66; }.transcript-list span { color: #6eaefe; font-size: 11px; font-weight: 800; }.transcript-list p { margin: 5px 0 0; color: #d5e1f3; font-size: 13px; line-height: 1.55; }
.rail-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }.rail-actions button { min-height: 40px; border: 1px solid #365173; border-radius: 10px; background: #173053; color: white; cursor: pointer; }.rail-actions .danger { color: #ffb2b2; }
.answer-workspace { height: 100%; min-width: 0; padding: 26px 30px 50px; overflow-y: auto; }.notice-strip { margin-bottom: 12px; padding: 11px 14px; border-radius: 10px; background: #e3f0ff; color: #285d99; }.notice-strip.error { background: #fff0f0; color: #a93c3c; }
.current-question-card { padding: 26px 28px; border-radius: 18px; background: #176fe5; color: white; box-shadow: 0 18px 38px rgba(32,92,172,.19); }.question-meta { display: flex; justify-content: space-between; color: #c9e0ff; font-size: 12px; font-weight: 700; }.question-meta em { font-style: normal; }.current-question-card h1 { margin: 18px 0 24px; max-width: 1100px; font-size: clamp(25px, 3vw, 40px); line-height: 1.35; letter-spacing: -.035em; }.question-actions { display: flex; gap: 8px; }.question-actions button { padding: 9px 14px; border: 1px solid rgba(255,255,255,.35); border-radius: 9px; background: rgba(255,255,255,.12); color: white; cursor: pointer; }.question-actions button:disabled { opacity: .45; cursor: not-allowed; }
.answer-grid { margin-top: 18px; display: grid; grid-template-columns: minmax(280px, .72fr) minmax(460px, 1.45fr); gap: 18px; }.quick-answer-card, .full-answer-card, .history-section { border: 1px solid #d9e3f1; border-radius: 16px; background: white; box-shadow: 0 9px 24px rgba(34,67,107,.06); }.quick-answer-card, .full-answer-card { min-height: 330px; padding: 22px; }.quick-answer-card header, .full-answer-card header { display: flex; align-items: center; justify-content: space-between; }.quick-answer-card header span, .full-answer-card header span { font-weight: 800; }.quick-answer-card small, .full-answer-card small { color: #7a8ca4; }.quick-answer-card ol { padding-left: 25px; }.quick-answer-card li { margin: 16px 0; padding-left: 5px; line-height: 1.65; }.full-answer-card header > div { display: flex; gap: 10px; align-items: baseline; }.answer-text { margin-top: 20px; color: #263b59; font-size: 16px; line-height: 1.86; white-space: pre-wrap; }.typing-dot { width: 9px; height: 9px; border-radius: 50%; background: #176fe5; animation: breathe 1s ease-in-out infinite; }
.history-section { margin-top: 18px; padding: 22px; }.history-section > header span { color: #7a8ca4; font-size: 12px; }.history-list { margin-top: 12px; display: grid; gap: 8px; }.history-list details { padding: 14px 16px; border-radius: 11px; background: #f4f7fb; }.history-list summary { display: flex; gap: 12px; cursor: pointer; font-weight: 700; }.history-list summary span { color: #176fe5; }.history-list p { margin: 14px 0 2px; color: #435773; line-height: 1.75; white-space: pre-wrap; }.empty-copy { color: #8292a8; line-height: 1.7; }
.archive-backdrop { position: fixed; z-index: 80; inset: 0; display: grid; place-items: center; padding: 24px; background: rgba(7, 24, 52, .58); backdrop-filter: blur(5px); }
.archive-dialog { width: min(640px, 100%); max-height: calc(100vh - 48px); overflow-y: auto; border: 1px solid rgba(190, 211, 238, .85); border-radius: 22px; background: #fff; color: #10213e; box-shadow: 0 32px 90px rgba(5, 28, 66, .28); font-family: "Microsoft YaHei", "PingFang SC", sans-serif; }
.archive-dialog-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; border-bottom: 1px solid #e5ecf5; padding: 24px 26px 20px; }.archive-dialog-header > div { display: flex; align-items: flex-start; gap: 14px; }.archive-mark { display: grid; width: 42px; height: 42px; flex: none; place-items: center; border-radius: 13px; background: #176fe5; color: white; font-size: 17px; font-weight: 900; box-shadow: 0 10px 20px rgba(23,111,229,.2); }.archive-dialog-header h2 { margin: 1px 0 7px; font-size: 23px; letter-spacing: -.03em; }.archive-dialog-header p { margin: 0; color: #6b7e99; font-size: 13px; line-height: 1.55; }.archive-dialog-header > button { width: 34px; height: 34px; flex: none; border: 0; border-radius: 50%; background: #f1f5fa; color: #60728b; cursor: pointer; font-size: 22px; line-height: 1; }.archive-dialog-header > button:disabled { opacity: .45; cursor: not-allowed; }
.archive-form { display: grid; gap: 18px; padding: 22px 26px 26px; }.archive-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }.archive-fields label { display: grid; gap: 7px; color: #425570; font-size: 12px; font-weight: 800; }.archive-fields em { color: #d64c4c; font-style: normal; }.archive-fields input { width: 100%; min-height: 43px; border: 1px solid #d5e0ee; border-radius: 10px; outline: none; background: #fbfdff; color: #132746; padding: 9px 11px; font: inherit; font-size: 14px; }.archive-fields input:focus { border-color: #3986ed; box-shadow: 0 0 0 3px rgba(23,111,229,.1); }.archive-date-field { grid-column: 1 / -1; max-width: calc(50% - 7px); }
.archive-question-preview { border: 1px solid #dce7f4; border-radius: 15px; background: #f5f9ff; padding: 16px 18px; }.archive-question-preview > header { display: flex; align-items: center; justify-content: space-between; }.archive-question-preview header strong { font-size: 14px; }.archive-question-preview header span { color: #176fe5; font-size: 12px; font-weight: 800; }.archive-question-preview ol { display: grid; gap: 9px; margin: 14px 0 0; padding-left: 24px; color: #334966; font-size: 13px; line-height: 1.55; }.archive-question-preview p { margin: 14px 0 0; color: #7b8da5; font-size: 13px; }.archive-question-preview small { display: block; margin-top: 12px; color: #7085a1; }.archive-error { margin: 0; border-left: 3px solid #d64c4c; border-radius: 0 9px 9px 0; background: #fff1f1; color: #a43737; padding: 10px 12px; font-size: 13px; line-height: 1.55; }
.archive-form footer, .archive-success > div { display: flex; justify-content: flex-end; gap: 10px; }.archive-form button, .archive-success button { min-height: 42px; border-radius: 10px; padding: 0 17px; font: inherit; font-size: 13px; font-weight: 800; cursor: pointer; }.archive-secondary { border: 1px solid #d4dfec; background: #fff; color: #536782; }.archive-primary { border: 1px solid #176fe5; background: #176fe5; color: white; box-shadow: 0 10px 20px rgba(23,111,229,.16); }.archive-primary:disabled { border-color: #acbcd1; background: #acbcd1; box-shadow: none; cursor: not-allowed; }
.archive-success { display: grid; place-items: center; padding: 42px 28px 30px; text-align: center; }.archive-success > span { display: grid; width: 58px; height: 58px; place-items: center; border-radius: 50%; background: #e3f7ee; color: #19845f; font-size: 26px; font-weight: 900; }.archive-success h3 { margin: 17px 0 7px; font-size: 21px; }.archive-success p { margin: 0 0 25px; color: #6b7e99; }.archive-success > div { justify-content: center; }
.answer-pip-card { min-height: 100vh; padding: 18px; color: #10213e; background: #edf3fb; font-family: "Microsoft YaHei", sans-serif; }.answer-pip-card > header { display: flex; align-items: center; gap: 9px; }.answer-pip-card header small { margin-left: auto; color: #61748f; }.answer-pip-card section { margin-top: 14px; padding: 18px; border-radius: 14px; background: white; }.answer-pip-card section > span { color: #176fe5; font-size: 11px; font-weight: 800; }.answer-pip-card h1 { margin: 10px 0 0; font-size: 21px; line-height: 1.45; }.answer-pip-card .pip-answer { min-height: 360px; }.answer-pip-card p { white-space: pre-wrap; line-height: 1.75; }
@keyframes wave { 0%,100% { transform: scaleY(.55); } 50% { transform: scaleY(1); } } @keyframes breathe { 50% { opacity: .25; transform: scale(1.45); } } @keyframes progress-glint { 65%,100% { transform: translateX(100%); } }
@media (prefers-reduced-motion: reduce) { .brand-wave i, .question-pulse span, .typing-dot, .setup-progress-track > span::after { animation: none; } }
@media (max-width: 1050px) { .preparation-layout { grid-template-columns: 1fr; }.preparation-thesis { min-height: 48vh; }.live-layout { grid-template-columns: 280px 1fr; }.answer-grid { grid-template-columns: 1fr; } }
@media (max-width: 680px) { .preparation-heading { grid-template-columns: 1fr; }.hidden-viewport-stage { padding: 20px; }.hidden-viewport-card { padding: 30px 20px; }.hidden-viewport-actions { width: 100%; flex-direction: column; }.hidden-viewport-actions button { width: 100%; }.archive-backdrop { align-items: end; padding: 0; }.archive-dialog { max-height: 92dvh; border-radius: 22px 22px 0 0; }.archive-fields { grid-template-columns: 1fr; }.archive-date-field { grid-column: auto; max-width: none; }.archive-form footer { flex-direction: column-reverse; }.archive-form footer button { width: 100%; min-height: 46px; } }
</style>
