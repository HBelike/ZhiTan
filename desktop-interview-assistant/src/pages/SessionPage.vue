<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { endLiveSession, sendLiveEvent } from '../runtime'
import { sessionState } from '../stores/session'
import { applyServerEvent } from '../stores/session'

const router = useRouter()
const route = useRoute()
const transcriptPanel = ref<HTMLElement | null>(null)
const connectionLabel = computed(() => ({ idle: '等待连接', connecting: '正在连接', ready: '连接正常', closed: '已结束', error: '连接异常' }[sessionState.connection]))

watch(() => sessionState.utterances.length, async () => {
  await nextTick()
  transcriptPanel.value?.scrollTo({ top: transcriptPanel.value.scrollHeight, behavior: 'smooth' })
})

function manualAnswer() {
  sendLiveEvent({ type: 'answer.request', mode: 'manual' })
}

async function finish() {
  const id = sessionState.sessionId
  await endLiveSession()
  await router.push(`/live-interview/history/${id}`)
}

onMounted(() => {
  if (import.meta.env.DEV && route.query.preview === '1') {
    sessionState.sessionId = String(route.params.id)
    sessionState.connection = 'ready'
    sessionState.asrStatus = '正在转写'
    sessionState.interviewerLevel = .68
    sessionState.candidateLevel = .34
    applyServerEvent(sessionState, { type: 'transcript.final', role: 'interviewer', sequence: 1, text: '请结合你最近的项目解释 CAP theorem？' })
    applyServerEvent(sessionState, { type: 'transcript.final', role: 'candidate', sequence: 1, text: '我会先从业务对一致性和可用性的要求讲起。' })
    applyServerEvent(sessionState, { type: 'question.detected', question_version: 1, question: '请结合你最近的项目解释 CAP theorem？', intent: 'project_deep_dive' })
    applyServerEvent(sessionState, { type: 'answer.started', question_version: 1, attempt: 1 })
    applyServerEvent(sessionState, { type: 'answer.delta', question_version: 1, attempt: 1, delta: '直接结论：CAP 不是三选二的静态口诀，而是发生网络分区时，一致性与可用性之间必须做出的工程权衡。\n\n1. 先说明业务最不能接受的失败类型。\n2. 再解释数据一致性等级与恢复窗口。\n3. 用已确认的项目事实说明最终取舍。\n\n个人项目细节不足时，请替换为真实经历。' })
    applyServerEvent(sessionState, { type: 'answer.completed', question_version: 1, attempt: 1 })
  } else if (!sessionState.sessionId) void router.replace('/live-interview/setup')
})
</script>

<template>
  <main class="session-shell">
    <header class="session-header">
      <div><p class="utility-label">实时面试</p><h1>会话监护台</h1></div>
      <div class="status-cluster"><span :class="['status-dot', sessionState.connection]"></span><strong>{{ connectionLabel }}</strong><span>{{ sessionState.asrStatus }}</span><span>{{ sessionState.answerStatus }}</span></div>
      <button class="danger-button" type="button" @click="finish">结束面试</button>
    </header>

    <section class="sound-rails" aria-label="双音轨状态">
      <div class="sound-rail interviewer"><span>面试官 · 系统声音</span><i><b :style="{ transform: `scaleX(${Math.max(.02, sessionState.interviewerLevel)})` }"></b></i><em>{{ Math.round(sessionState.interviewerLevel * 100) }}</em></div>
      <div class="sound-rail candidate"><span>应试者 · 麦克风</span><i><b :style="{ transform: `scaleX(${Math.max(.02, sessionState.candidateLevel)})` }"></b></i><em>{{ Math.round(sessionState.candidateLevel * 100) }}</em></div>
    </section>

    <p v-if="sessionState.error" class="session-warning">{{ sessionState.error }}</p>
    <section class="live-grid">
      <article class="transcript-card">
        <header><div><p class="step-label">双方对话</p><h2>实时转写</h2></div><span>原文不自动翻译</span></header>
        <div ref="transcriptPanel" class="transcript-list">
          <p v-if="!sessionState.utterances.length" class="empty-copy">等待双方开始交谈。完整话语会固定在这里。</p>
          <div v-for="(item, index) in sessionState.utterances" :key="`${item.role}-${item.sequence}-${index}`" :class="['utterance', item.role]">
            <span>{{ item.role === 'interviewer' ? '面试官' : '应试者' }}</span><p>{{ item.text }}</p>
          </div>
          <div v-for="role in (['interviewer', 'candidate'] as const)" v-show="sessionState.partials[role]" :key="role" :class="['utterance', role, 'partial']"><span>{{ role === 'interviewer' ? '面试官' : '应试者' }} · 识别中</span><p>{{ sessionState.partials[role] }}</p></div>
        </div>
      </article>

      <article class="answer-card">
        <header><div><p class="step-label">当前问题</p><h2>{{ sessionState.question || '等待面试官提问' }}</h2></div><span v-if="sessionState.activeQuestionVersion">版本 {{ sessionState.activeQuestionVersion }}</span></header>
        <div class="answer-body"><p v-if="!sessionState.answerText" class="empty-copy">识别到完整问题后，中文回答骨架会在这里流式出现。</p><div v-else class="answer-copy">{{ sessionState.answerText }}</div></div>
        <footer>
          <button class="quiet-button" type="button" @click="manualAnswer">立即生成</button>
          <button class="quiet-button" type="button" @click="sendLiveEvent({ type: 'answer.cancel' })">停止生成</button>
          <button class="primary-button compact" type="button" :disabled="!sessionState.question" @click="sendLiveEvent({ type: 'answer.request', mode: 'regenerate' })">重新生成</button>
        </footer>
      </article>
    </section>
  </main>
</template>
