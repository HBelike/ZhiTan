<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listMicrophones } from '../audio/capture'
import { beginLiveSession } from '../runtime'
import { buildSessionRequest } from '../setup'
import { resetSessionState, sessionState } from '../stores/session'
import { bridge, isElectronBridge } from '../bridge'

interface SetupOptions {
  asr_models: Array<{ id: string; display_name: string; readiness: string }>
  answer_models: Array<{ id: string; display_name: string; readiness: string }>
  environment_asr: { readiness: string; blocked_reason: string | null; display_name: string }
}

const router = useRouter()
const route = useRoute()
const launchApiBaseUrl = typeof route.query.apiBaseUrl === 'string' ? route.query.apiBaseUrl : ''
const apiBaseUrl = ref(launchApiBaseUrl || localStorage.getItem('liveInterviewApiBase') || 'http://127.0.0.1:8000')
const options = ref<SetupOptions | null>(null)
const microphones = ref<Array<{ deviceId: string; label: string }>>([])
const asrModelId = ref('')
const answerModelId = ref('')
const microphoneId = ref('')
const loading = ref(false)
const error = ref('')
const readyAsr = computed(() => options.value?.asr_models.filter((item) => item.readiness === 'ready') ?? [])
const readyAnswers = computed(() => options.value?.answer_models.filter((item) => item.readiness === 'ready') ?? [])
const environmentAsrReady = computed(() => options.value?.environment_asr.readiness === 'ready')
const transcriptionReady = computed(() => environmentAsrReady.value || readyAsr.value.length > 0)

async function loadOptions() {
  loading.value = true
  error.value = ''
  try {
    options.value = await bridge.apiRequest<SetupOptions>(apiBaseUrl.value, '/api/career/live-interviews/setup-options')
    microphones.value = isElectronBridge
      ? await listMicrophones()
      : [{ deviceId: 'preview-microphone', label: '默认麦克风（预览）' }]
    asrModelId.value ||= readyAsr.value[0]?.id ?? ''
    answerModelId.value ||= readyAnswers.value[0]?.id ?? ''
    microphoneId.value ||= microphones.value[0]?.deviceId ?? ''
    if (!transcriptionReady.value) {
      error.value = options.value.environment_asr.blocked_reason || '尚未配置可用的实时转写模型。'
    }
    localStorage.setItem('liveInterviewApiBase', apiBaseUrl.value)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法完成设备与服务预检'
  } finally {
    loading.value = false
  }
}

function openLogin() {
  void bridge.openLogin(apiBaseUrl.value)
}

async function start() {
  if (!transcriptionReady.value) {
    error.value = options.value?.environment_asr.blocked_reason || '尚未配置可用的实时转写模型。'
    return
  }
  loading.value = true
  error.value = ''
  try {
    resetSessionState()
    const response = await bridge.apiRequest<{ session: { id: string } }>(
      apiBaseUrl.value,
      '/api/career/live-interviews/sessions',
      {
        method: 'POST',
        body: buildSessionRequest(asrModelId.value, answerModelId.value),
      },
    )
    await beginLiveSession(apiBaseUrl.value, response.session.id, microphoneId.value || undefined)
    await router.push(`/live-interview/session/${response.session.id}`)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法开始面试'
    sessionState.connection = 'error'
  } finally {
    loading.value = false
  }
}

onMounted(loadOptions)
</script>

<template>
  <main class="setup-shell">
    <section class="setup-intro">
      <div class="brand-mark" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
      <p class="utility-label">Windows 10/11 · 双音轨实时辅助</p>
      <h1>听清面试官的问题，<br />实时生成中文解法。</h1>
      <p class="intro-copy">系统声音固定识别为面试官，转写后的问题直接交给所选回答模型。麦克风仅用于显示应试者转写，不向回答模型注入简历、岗位或面经资料。</p>
      <div class="privacy-note"><strong>采集边界</strong><span>点击“开始面试”后才采集；结束或断线立即停止；不保存原始音频。</span></div>
    </section>

    <section class="setup-panel" aria-labelledby="setup-title">
      <header><p class="step-label">开始准备</p><h2 id="setup-title">检查模型与声音</h2></header>
      <label>服务地址<input v-model="apiBaseUrl" spellcheck="false" /></label>
      <div class="inline-actions"><button class="quiet-button" type="button" @click="openLogin">打开登录页</button><button class="quiet-button" type="button" :disabled="loading" @click="loadOptions">重新检查</button></div>
      <div class="field-grid">
        <label>实时转写<select v-model="asrModelId"><option v-if="environmentAsrReady" value="">{{ options?.environment_asr.display_name }}</option><option v-else-if="!readyAsr.length" value="" disabled>未配置实时转写模型</option><option v-for="item in readyAsr" :key="item.id" :value="item.id">{{ item.display_name }}</option></select></label>
        <label>回答模型<select v-model="answerModelId"><option value="">免费模型自动选择</option><option v-for="item in readyAnswers" :key="item.id" :value="item.id">{{ item.display_name }}</option></select></label>
      </div>
      <label>麦克风<select v-model="microphoneId"><option v-for="item in microphones" :key="item.deviceId" :value="item.deviceId">{{ item.label || '麦克风' }}</option></select></label>
      <div class="question-source"><span>回答依据</span><strong>仅使用面试官实时转写的问题文本</strong><small>不读取简历、目标岗位或面经资料</small></div>
      <div class="output-row"><span>系统输出</span><strong>Windows 当前默认播放设备</strong><small>开始时由系统授权窗口确认</small></div>
      <p v-if="error" class="error-message">{{ error }}</p>
      <button class="primary-button" type="button" :disabled="loading || !transcriptionReady" @click="start">{{ loading ? '正在检查…' : '开始面试' }}</button>
    </section>
  </main>
</template>
