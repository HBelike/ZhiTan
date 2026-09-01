<script setup>
import { computed, onMounted, ref } from 'vue'

const loading = ref(true)
const status = ref(null)
const errorMessage = ref('')

const consoleUrl = computed(() => status.value?.ui_url || 'https://smith.langchain.com')

onMounted(loadStatus)

async function loadStatus() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await fetch('/api/observability/status', { credentials: 'include', cache: 'no-store' })
    if (!response.ok) throw new Error(await responseError(response, '无法读取 LangSmith 配置'))
    status.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法读取 LangSmith 配置'
  } finally {
    loading.value = false
  }
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
  <section class="observability-page">
    <header class="observability-heading">
      <div>
        <p class="eyebrow">LANGSMITH OBSERVABILITY</p>
        <h2>模型链路监控</h2>
        <p>查看 LangChain 与手工 Trace 记录的模型调用、Token 消耗、耗时和失败原因。</p>
      </div>
      <div class="observability-actions">
        <button class="secondary-button" type="button" @click="loadStatus">刷新状态</button>
        <a class="refresh-button observability-open-button" :href="consoleUrl" target="_blank" rel="noopener noreferrer">打开 LangSmith</a>
      </div>
    </header>

    <section v-if="loading" class="observability-state">正在连接 LangSmith…</section>
    <section v-else-if="errorMessage" class="observability-state danger">
      <strong>无法加载监控页面</strong>
      <span>{{ errorMessage }}</span>
    </section>
    <template v-else>
      <section class="observability-status-grid">
        <article>
          <span>追踪状态</span>
          <strong :class="{ ready: status.enabled }">{{ status.enabled ? '已启用' : '待配置' }}</strong>
          <small>{{ status.enabled ? '服务端已检测到 LangSmith 凭证。' : '配置 LANGSMITH_API_KEY 后自动启用。' }}</small>
        </article>
        <article>
          <span>项目</span>
          <strong>{{ status.project }}</strong>
          <small>运行记录按项目维度归档。</small>
        </article>
        <article>
          <span>控制台访问</span>
          <strong>外部页面</strong>
          <small>LangSmith 不支持嵌入工作台，需在新标签页中打开。</small>
        </article>
      </section>

      <section class="observability-console-card" aria-label="LangSmith 控制台入口">
        <div class="observability-console-mark" aria-hidden="true">↗</div>
        <div class="observability-console-copy">
          <p class="eyebrow">EXTERNAL CONSOLE</p>
          <h3>在 LangSmith 中查看完整追踪记录</h3>
          <p>LangSmith 的页面安全策略禁止被 iframe 嵌入，因此工作台只展示当前连接状态。点击下方入口可在新标签页登录并查看 Trace、耗时与运行详情。</p>
          <code>{{ consoleUrl }}</code>
        </div>
        <a class="secondary-button observability-console-link" :href="consoleUrl" target="_blank" rel="noopener noreferrer">在新标签页打开控制台 ↗</a>
      </section>
    </template>
  </section>
</template>
