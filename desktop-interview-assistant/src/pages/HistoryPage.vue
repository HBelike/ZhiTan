<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { sessionState } from '../stores/session'
import { bridge } from '../bridge'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const error = ref('')
const history = ref<{ session: Record<string, unknown>; utterances: Array<Record<string, unknown>>; answers: Array<Record<string, unknown>> } | null>(null)

onMounted(async () => {
  try {
    history.value = await bridge.apiRequest(sessionState.apiBaseUrl, `/api/career/live-interviews/sessions/${route.params.id}/history`)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取会话记录'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="history-shell">
    <header class="history-header"><div><p class="utility-label">会后记录</p><h1>这次面试说了什么</h1></div><button class="primary-button compact" type="button" @click="router.push('/live-interview/setup')">准备下一场</button></header>
    <p v-if="loading" class="empty-copy">正在整理最终转写和有效回答…</p>
    <p v-else-if="error" class="error-message">{{ error }}</p>
    <section v-else-if="history" class="history-grid">
      <article><header><p class="step-label">对话时间线</p><h2>最终转写</h2></header><div class="history-list"><div v-for="item in history.utterances" :key="String(item.id)" class="history-item"><span>{{ item.role === 'interviewer' ? '面试官' : '应试者' }}</span><p>{{ item.corrected_text || item.raw_text }}</p><small v-if="item.corrected_text">原文：{{ item.raw_text }}</small></div></div></article>
      <article><header><p class="step-label">回答记录</p><h2>问题与建议</h2></header><div class="history-list"><div v-for="item in history.answers" :key="String(item.id)" class="history-item answer"><span>问题 {{ item.question_version }} · {{ item.status }}</span><h3>{{ item.normalized_question }}</h3><p>{{ item.answer_text || '本次未形成完整回答' }}</p></div></div></article>
    </section>
  </main>
</template>
