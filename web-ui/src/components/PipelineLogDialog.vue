<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  run: { type: Object, required: true }
})
const emit = defineEmits(['close'])

const STREAM_EVENT_TYPES = [
  'run_started',
  'task_started',
  'log',
  'task_succeeded',
  'task_failed',
  'run_succeeded',
  'run_failed'
]
const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled'])

const events = ref([])
const currentRun = ref({ ...props.run })
const loading = ref(true)
const errorMessage = ref('')
const connectionState = ref('正在连接')
const lastEventId = ref(0)
const logViewport = ref(null)
const followTail = ref(true)
const seenEventIds = new Set()
let eventSource = null

const taskStates = computed(() => {
  const tasks = new Map()
  for (const event of events.value) {
    if (!event.task_name || !['task_started', 'task_succeeded', 'task_failed'].includes(event.event_type)) continue
    const taskKey = event.task_run_id || event.task_name
    const existing = tasks.get(taskKey) ?? {
      name: event.task_name,
      runId: event.task_run_id || '',
      status: 'running'
    }
    if (event.task_run_id) existing.runId = event.task_run_id
    if (event.event_type === 'task_started') existing.status = 'running'
    if (event.event_type === 'task_succeeded') existing.status = 'succeeded'
    if (event.event_type === 'task_failed') existing.status = 'failed'
    tasks.set(taskKey, existing)
  }
  return [...tasks.values()]
})

const statusText = computed(() => ({
  queued: '排队中',
  running: '运行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消'
})[currentRun.value.status] ?? currentRun.value.status ?? '未知')

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
  void loadLogs()
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown)
  closeStream()
})

async function loadLogs() {
  loading.value = true
  errorMessage.value = ''
  try {
    let afterId = 0
    while (true) {
      const response = await fetch(`/api/admin/pipeline-runs/${props.run.id}/logs?after_id=${afterId}&limit=1000`, {
        credentials: 'include',
        cache: 'no-store'
      })
      if (!response.ok) throw new Error(await responseError(response, '无法读取工作流日志'))
      const payload = await response.json()
      if (payload.item) currentRun.value = payload.item
      const batch = payload.events ?? []
      appendEvents(batch)
      if (batch.length < 1000) break
      afterId = lastEventId.value
    }
    if (TERMINAL_STATUSES.has(currentRun.value.status)) connectionState.value = '日志已完整加载'
    else openStream()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法读取工作流日志'
    connectionState.value = '连接失败'
  } finally {
    loading.value = false
  }
}

function openStream() {
  closeStream()
  connectionState.value = '正在连接'
  const streamUrl = `/api/admin/pipeline-runs/${props.run.id}/logs/stream?after_id=${lastEventId.value}`
  eventSource = new EventSource(streamUrl)
  eventSource.onopen = () => {
    connectionState.value = '实时更新中'
  }
  eventSource.onerror = () => {
    if (TERMINAL_STATUSES.has(currentRun.value.status)) {
      closeStream()
      connectionState.value = '日志已完整加载'
      return
    }
    connectionState.value = '连接中断，正在重连'
  }
  for (const eventType of STREAM_EVENT_TYPES) {
    eventSource.addEventListener(eventType, receiveStreamEvent)
  }
}

function receiveStreamEvent(sourceEvent) {
  try {
    const event = JSON.parse(sourceEvent.data)
    if (sourceEvent.lastEventId) event.id = Number(sourceEvent.lastEventId)
    appendEvents([event])
    if (event.event_type === 'run_succeeded') finishStream('succeeded')
    if (event.event_type === 'run_failed') finishStream('failed')
  } catch {
    errorMessage.value = '收到了一条无法解析的实时日志'
  }
}

function finishStream(status) {
  currentRun.value = { ...currentRun.value, status }
  closeStream()
  connectionState.value = '日志已完整加载'
}

function appendEvents(incoming) {
  const accepted = []
  for (const item of incoming) {
    const id = Number(item?.id)
    if (!Number.isFinite(id) || seenEventIds.has(id)) continue
    seenEventIds.add(id)
    lastEventId.value = Math.max(lastEventId.value, id)
    accepted.push({ ...item, id })
  }
  if (!accepted.length) return
  events.value.push(...accepted)
  if (followTail.value) void scrollToTail()
}

async function scrollToTail() {
  await nextTick()
  if (!followTail.value || !logViewport.value) return
  logViewport.value.scrollTop = logViewport.value.scrollHeight
}

function trackScroll() {
  const viewport = logViewport.value
  if (!viewport) return
  followTail.value = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 48
}

function resumeTail() {
  followTail.value = true
  void scrollToTail()
}

function closeStream() {
  if (!eventSource) return
  for (const eventType of STREAM_EVENT_TYPES) {
    eventSource.removeEventListener(eventType, receiveStreamEvent)
  }
  eventSource.close()
  eventSource = null
}

function closeDialog() {
  closeStream()
  emit('close')
}

function handleKeydown(event) {
  if (event.key === 'Escape') closeDialog()
}

function formatTime(value) {
  if (!value) return '--:--:--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

function taskStatusText(status) {
  return ({ running: '运行中', succeeded: '成功', failed: '失败' })[status] ?? status
}

async function responseError(response, fallback) {
  try {
    const payload = await response.json()
    return payload?.detail || fallback
  } catch {
    return fallback
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="pipeline-log-overlay" @click.self="closeDialog">
      <section class="pipeline-log-dialog" role="dialog" aria-modal="true" aria-labelledby="pipeline-log-title">
        <header class="pipeline-log-header">
          <div>
            <div class="pipeline-log-title-row">
              <h2 id="pipeline-log-title">工作流日志 #{{ String(run.id).slice(0, 8) }}</h2>
              <span class="manual-run-status" :class="`is-${currentRun.status}`">{{ statusText }}</span>
            </div>
            <p>{{ connectionState }} · 已接收 {{ events.length }} 条日志</p>
          </div>
          <button type="button" class="pipeline-log-close" aria-label="关闭工作流日志" @click="closeDialog">×</button>
        </header>

        <div v-if="errorMessage" class="pipeline-log-error" role="alert">{{ errorMessage }}</div>

        <section class="pipeline-task-chain" aria-label="任务链路">
          <div class="pipeline-log-section-title">
            <strong>任务链路</strong>
            <span>{{ taskStates.length }} 个任务</span>
          </div>
          <div v-if="taskStates.length" class="pipeline-task-track">
            <article v-for="task in taskStates" :key="`${task.name}-${task.runId}`" :class="`is-${task.status}`">
              <i aria-hidden="true"></i>
              <div>
                <strong>{{ task.name }}</strong>
                <span>{{ taskStatusText(task.status) }}</span>
              </div>
            </article>
          </div>
          <p v-else class="pipeline-log-waiting">等待首个任务开始…</p>
        </section>

        <section class="pipeline-log-console">
          <div class="pipeline-log-section-title">
            <strong>实时日志</strong>
            <button v-if="!followTail" type="button" @click="resumeTail">回到最新日志</button>
          </div>
          <div ref="logViewport" class="pipeline-log-lines" @scroll="trackScroll">
            <p v-if="loading" class="pipeline-log-waiting">正在读取历史日志…</p>
            <p v-else-if="!events.length" class="pipeline-log-waiting">暂无日志，等待执行器输出…</p>
            <article v-for="event in events" :key="event.id" class="pipeline-log-line" :class="`is-${String(event.level).toLowerCase()}`">
              <time :datetime="event.created_at">{{ formatTime(event.created_at) }}</time>
              <span>{{ event.level }}</span>
              <em>{{ event.task_name || '工作流' }}</em>
              <p>{{ event.message }}</p>
            </article>
          </div>
        </section>
      </section>
    </div>
  </Teleport>
</template>
