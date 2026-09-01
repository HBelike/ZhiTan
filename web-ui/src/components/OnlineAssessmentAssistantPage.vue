<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { assessmentApi } from '../online-assessment/api.js'
import {
  assessmentBridge,
  captureToApiPayload,
  mergeAssessmentCaptures
} from '../online-assessment/bridge.js'
import { confirmAssessmentProblem, createAssessmentState } from '../online-assessment/state.js'
import {
  BOSS_EXTENSION_DOWNLOAD_URL,
  BOSS_EXTENSION_VERSION
} from '../boss-extension-onboarding.js'

const manager = createAssessmentState(window.sessionStorage)
manager.restore()

const phase = ref(manager.state.phase)
const problem = ref(manager.state.problem)
const solution = ref(manager.state.solution)
const tests = ref(manager.state.tests || [])
const report = ref(manager.state.report)
const assessmentItems = ref(manager.state.items || [])
const captureToken = ref(manager.state.captureToken || '')
const sourceTabId = ref(manager.state.sourceTabId ?? null)
const extensionReady = ref(false)
const extensionChecked = ref(false)
const extensionDetecting = ref(false)
const extensionVersion = ref('')
const errorMessage = ref('')
const noticeMessage = ref('')
const showArchive = ref(false)
const archiveBusy = ref(false)
const archiveSuccessId = ref('')
const archiveForm = ref({
  company_name: '',
  role_name: '',
  assessment_date: new Date().toISOString().slice(0, 10)
})
let lastCapture = null
let activeController = null
let pendingLaunchId = new URLSearchParams(window.location.search).get('launch_id') || ''

const phaseLabels = {
  waiting_for_capture: '等待题面',
  capturing: '读取题面',
  analyzing: '分析题意',
  generating: '生成答案',
  testing: '执行测试',
  repairing: '修复代码',
  ready: '测试通过',
  partial: '需要复核',
  failed: '本轮失败'
}

const busy = computed(() => ['capturing', 'analyzing', 'generating', 'testing', 'repairing'].includes(phase.value))
const phaseLabel = computed(() => phaseLabels[phase.value] || '等待题面')
const confidencePercent = computed(() => Math.round(Number(problem.value?.confidence || 0) * 100))
const passedTotal = computed(() => Number(report.value?.passed_count || 0))
const testTotal = computed(() => passedTotal.value + Number(report.value?.failed_count || 0))
const canSolve = computed(() => Boolean(problem.value?.statement && !busy.value))
const currentLanguage = computed(() => solution.value?.language || problem.value?.language || 'python')
const pipelineSteps = computed(() => {
  const order = ['analyzing', 'generating', 'testing']
  const currentIndex = order.indexOf(phase.value === 'repairing' ? 'testing' : phase.value)
  const ready = phase.value === 'ready'
  return [
    { key: 'analyzing', label: '识别题意' },
    { key: 'generating', label: '生成代码' },
    { key: 'testing', label: phase.value === 'repairing' ? '自动修复' : '运行测试' }
  ].map((step, index) => ({
    ...step,
    status: ready || currentIndex > index ? 'done' : currentIndex === index ? 'active' : 'waiting'
  }))
})

function syncManager(patch, runVersion = manager.state.runVersion) {
  if (!manager.accept(runVersion, patch)) return false
  phase.value = manager.state.phase
  problem.value = manager.state.problem
  solution.value = manager.state.solution
  tests.value = manager.state.tests || []
  report.value = manager.state.report
  assessmentItems.value = manager.state.items || []
  captureToken.value = manager.state.captureToken || ''
  sourceTabId.value = manager.state.sourceTabId ?? null
  return true
}

function beginRun(nextPhase, { clearSolution = false } = {}) {
  activeController?.abort()
  activeController = new AbortController()
  const runVersion = manager.begin(nextPhase)
  const patch = { phase: nextPhase, tests: [], report: null }
  if (clearSolution) patch.solution = null
  syncManager(patch, runVersion)
  errorMessage.value = ''
  noticeMessage.value = ''
  return runVersion
}

async function prepareCapture(rawCapture) {
  const capture = { ...rawCapture }
  if (!capture.screenshotDataUrl) return capture
  try {
    capture.screenshotDataUrl = await resizeScreenshot(capture.screenshotDataUrl)
  } catch {
    capture.screenshotDataUrl = ''
  }
  return capture
}

function resizeScreenshot(dataUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => {
      const maximum = 1920
      const scale = Math.min(1, maximum / Math.max(image.naturalWidth, image.naturalHeight))
      if (scale === 1 && dataUrl.length <= 7_000_000) {
        resolve(dataUrl)
        return
      }
      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale))
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale))
      canvas.getContext('2d')?.drawImage(image, 0, 0, canvas.width, canvas.height)
      const resized = canvas.toDataURL('image/png')
      resolve(resized.length <= 7_000_000 ? resized : '')
    }
    image.onerror = reject
    image.src = dataUrl
  })
}

async function processCapture(rawCapture, { append = false } = {}) {
  const runVersion = beginRun('analyzing', { clearSolution: true })
  const prepared = await prepareCapture(rawCapture)
  if (runVersion !== manager.state.runVersion) return
  lastCapture = append && lastCapture ? mergeAssessmentCaptures(lastCapture, prepared) : prepared
  try {
    const analyzed = await assessmentApi.analyze(
      runVersion,
      captureToApiPayload(lastCapture),
      activeController.signal
    )
    if (analyzed.run_version !== runVersion) return
    lastCapture = { ...lastCapture, screenshotDataUrl: '' }
    const accepted = syncManager({
      phase: analyzed.problem.incomplete_reasons?.length || analyzed.problem.confidence < 0.65 ? 'partial' : 'generating',
      problem: analyzed.problem,
      solution: null,
      tests: [],
      report: null
    }, runVersion)
    if (!accepted) return
    if (phase.value === 'partial') {
      noticeMessage.value = '题面可能不完整。可补充题面，或修正左侧内容后点击“确认题面并解答”。'
      return
    }
    await generateSolution(runVersion)
  } catch (error) {
    handleRunError(error, runVersion)
  }
}

async function generateSolution(existingVersion = null) {
  if (!problem.value) return
  const runVersion = existingVersion ?? beginRun('generating', { clearSolution: true })
  syncManager({ phase: 'generating', problem: problem.value, solution: null, tests: [], report: null }, runVersion)
  try {
    await assessmentApi.solve(
      runVersion,
      problem.value,
      (event) => {
        if (event.run_version !== manager.state.runVersion) return
        if (event.type === 'needs_confirmation') {
          syncManager({ phase: 'partial' }, runVersion)
          noticeMessage.value = '题面仍不完整，请补充输入输出格式或函数签名。'
        }
        if (event.type === 'solution') {
          syncManager({ phase: 'testing', solution: event.solution }, runVersion)
        }
        if (event.type === 'error') throw new Error(event.message)
      },
      activeController.signal
    )
    if (runVersion !== manager.state.runVersion || !solution.value) return
    await executeCurrent(runVersion, true)
  } catch (error) {
    handleRunError(error, runVersion)
  }
}

async function executeCurrent(existingVersion = null, autoRepair = false) {
  if (!problem.value || !solution.value) return
  const runVersion = existingVersion ?? beginRun('testing')
  syncManager({ phase: autoRepair ? 'repairing' : 'testing', problem: problem.value, solution: solution.value }, runVersion)
  try {
    const payload = await assessmentApi.execute(
      runVersion,
      problem.value,
      solution.value,
      tests.value,
      autoRepair,
      activeController.signal
    )
    if (payload.run_version !== runVersion) return
    const finalPhase = payload.report.final_status === 'passed' ? 'ready' : 'partial'
    const nextItems = upsertAssessmentItem(assessmentItems.value, {
      problem: problem.value,
      solution: payload.solution,
      report: payload.report
    })
    syncManager({
      phase: finalPhase,
      solution: payload.solution,
      tests: payload.tests,
      report: payload.report,
      items: nextItems
    }, runVersion)
    noticeMessage.value = finalPhase === 'ready'
      ? `已通过 ${payload.report.passed_count} 个可见测试。隐藏测试仍需以平台结果为准。`
      : '代码已生成，但仍有测试未通过。你可以编辑代码后重新测试。'
  } catch (error) {
    if (String(error?.message || '').includes('代码执行器未就绪')) {
      const nextItems = upsertAssessmentItem(assessmentItems.value, {
        problem: problem.value,
        solution: solution.value,
        report: null
      })
      syncManager({ phase: 'partial', items: nextItems }, runVersion)
      errorMessage.value = `${error.message}；答案仍可复制，启动执行器后可重新测试。`
      return
    }
    handleRunError(error, runVersion)
  }
}

function upsertAssessmentItem(items, incoming) {
  const key = `${incoming.problem.source_url || ''}\n${incoming.problem.title}\n${incoming.problem.statement}`
  const next = [...items]
  const index = next.findIndex((item) => (
    `${item.problem.source_url || ''}\n${item.problem.title}\n${item.problem.statement}` === key
  ))
  if (index >= 0) next[index] = incoming
  else next.push(incoming)
  return next.slice(-50)
}

function handleRunError(error, runVersion) {
  if (error?.name === 'AbortError' || runVersion !== manager.state.runVersion) return
  syncManager({ phase: problem.value || solution.value ? 'partial' : 'failed' }, runVersion)
  errorMessage.value = error instanceof Error ? error.message : '本轮处理失败，请重新生成。'
}

async function recapture(append = false) {
  if (!captureToken.value) {
    errorMessage.value = '题目标签页连接不存在，请回到题目页重新点击扩展图标。'
    return
  }
  const runVersion = beginRun('capturing')
  try {
    const payload = append
      ? await assessmentBridge.append(captureToken.value)
      : await assessmentBridge.refresh(captureToken.value)
    if (runVersion !== manager.state.runVersion) return
    syncManager({ captureToken: payload.captureToken, sourceTabId: payload.sourceTabId }, runVersion)
    await processCapture(payload.capture, { append })
  } catch (error) {
    handleRunError(error, runVersion)
  }
}

function confirmProblemAndSolve() {
  if (!problem.value) return
  const confirmed = confirmAssessmentProblem(problem.value)
  syncManager({ problem: confirmed })
  generateSolution()
}

async function copyCode() {
  if (!solution.value?.code) return
  try {
    await navigator.clipboard.writeText(solution.value.code)
    noticeMessage.value = '代码已复制。请自行粘贴到笔试平台并确认语言与函数签名。'
  } catch {
    errorMessage.value = '浏览器未允许复制，请手动选择代码。'
  }
}

function persistEditedSolution() {
  syncManager({ solution: solution.value })
}

function persistEditedProblem() {
  syncManager({ problem: problem.value })
}

function archivePayload() {
  const items = assessmentItems.value.length
    ? assessmentItems.value
    : problem.value && solution.value
      ? [{ problem: problem.value, solution: solution.value, report: report.value }]
      : []
  return {
    ...archiveForm.value,
    assessment_date: archiveForm.value.assessment_date || null,
    items
  }
}

async function archiveAssessment() {
  archiveBusy.value = true
  errorMessage.value = ''
  try {
    const result = await assessmentApi.archive(archivePayload())
    archiveSuccessId.value = result.experience_id
    noticeMessage.value = `已把本场 ${archivePayload().items.length} 道题保存到面经库。`
    showArchive.value = false
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存到面经库失败。'
  } finally {
    archiveBusy.value = false
  }
}

function testResult(testId) {
  return report.value?.tests?.find((item) => item.test_id === testId) || null
}

function resetSession() {
  activeController?.abort()
  manager.clear()
  lastCapture = null
  phase.value = manager.state.phase
  problem.value = null
  solution.value = null
  tests.value = []
  report.value = null
  assessmentItems.value = []
  captureToken.value = ''
  sourceTabId.value = null
  errorMessage.value = ''
  noticeMessage.value = ''
}

async function detectExtension() {
  if (extensionDetecting.value) return
  extensionDetecting.value = true
  errorMessage.value = ''
  try {
    const ping = await assessmentBridge.ping()
    extensionVersion.value = ping.version || ''
    extensionReady.value = ping.capabilities?.includes('online_assessment_capture_v1') === true
    if (!extensionReady.value) {
      errorMessage.value = extensionVersion.value
        ? `检测到浏览器助手 ${extensionVersion.value}，线上笔试需要 ${BOSS_EXTENSION_VERSION} 或更高版本。`
        : `浏览器助手缺少线上笔试能力，请更新到 ${BOSS_EXTENSION_VERSION}。`
      return
    }
    if (!pendingLaunchId) {
      noticeMessage.value = `浏览器助手 ${extensionVersion.value || BOSS_EXTENSION_VERSION} 已连接。`
      return
    }
    const claimed = await assessmentBridge.claim(pendingLaunchId)
    pendingLaunchId = ''
    captureToken.value = claimed.captureToken
    sourceTabId.value = claimed.sourceTabId
    syncManager({ captureToken: claimed.captureToken, sourceTabId: claimed.sourceTabId })
    window.history.replaceState({}, '', '/career/online-assessment')
    await processCapture(claimed.capture)
  } catch (error) {
    extensionReady.value = false
    if (pendingLaunchId || !problem.value) {
      errorMessage.value = error instanceof Error ? error.message : '无法连接浏览器助手。'
    }
  } finally {
    extensionChecked.value = true
    extensionDetecting.value = false
  }
}

function retryExtensionDetection() {
  noticeMessage.value = ''
  void detectExtension()
}

onMounted(async () => {
  syncManager({})
  await detectExtension()
})

onBeforeUnmount(() => activeController?.abort())
</script>

<template>
  <main class="assessment-page">
    <header class="assessment-header">
      <div class="assessment-brand">
        <span aria-hidden="true">{ }</span>
        <div>
          <strong>线上笔试助手</strong>
          <small>只读分析 · 不自动填写或提交</small>
        </div>
      </div>

      <ol class="assessment-runway" aria-label="本轮处理进度">
        <li v-for="step in pipelineSteps" :key="step.key" :class="`is-${step.status}`">
          <i aria-hidden="true"></i>
          <span>{{ step.label }}</span>
        </li>
      </ol>

      <div class="assessment-header-status">
        <span :class="`phase-${phase}`">{{ phaseLabel }}</span>
        <small v-if="extensionVersion">扩展 {{ extensionVersion }}</small>
      </div>
    </header>

    <section v-if="errorMessage || noticeMessage" class="assessment-notices" aria-live="polite">
      <p v-if="errorMessage" class="is-error">{{ errorMessage }}</p>
      <p v-if="noticeMessage" class="is-notice">{{ noticeMessage }}</p>
    </section>

    <section v-if="!problem && phase === 'waiting_for_capture'" class="assessment-empty">
      <div class="empty-glyph" aria-hidden="true"><span>⌖</span></div>
      <template v-if="extensionChecked && !extensionReady">
        <h1>更新浏览器助手后继续</h1>
        <p>当前扩展{{ extensionVersion ? `为 ${extensionVersion}` : '未连接' }}，线上笔试识别需要 {{ BOSS_EXTENSION_VERSION }}。更新后不用重新打开本页，直接重新检测即可。</p>
        <div class="extension-recovery-actions">
          <a :href="BOSS_EXTENSION_DOWNLOAD_URL" download>下载最新版扩展</a>
          <button type="button" :disabled="extensionDetecting" @click="retryExtensionDetection">
            {{ extensionDetecting ? '正在检测…' : '重新检测' }}
          </button>
          <a class="secondary" href="/boss-extension-guide.html" target="_blank" rel="noopener">查看安装说明</a>
        </div>
        <small>解压覆盖后，请在浏览器扩展管理页点击一次“重新加载”。</small>
      </template>
      <template v-else>
        <h1>从题目页开始，不从这里上传</h1>
        <p>打开在线笔试题目所在标签页，点击浏览器右上角的“求职浏览器助手”。扩展会读取当前可见题面并自动打开这里。</p>
        <ol>
          <li><b>1</b><span>切到题目标签页</span></li>
          <li><b>2</b><span>点击扩展图标</span></li>
          <li><b>3</b><span>等待代码与测试结果</span></li>
        </ol>
        <small>支持 Python、JavaScript、Java、C++；不读取隐藏测试，不代替平台提交。</small>
      </template>
    </section>

    <section v-else class="assessment-workbench" :aria-busy="busy">
      <article class="assessment-panel problem-panel">
        <header>
          <div>
            <span class="panel-index">题面</span>
            <strong>{{ problem?.source_platform || '当前页面' }}</strong>
          </div>
          <span v-if="problem" class="confidence" :class="{ low: confidencePercent < 65 }">可信度 {{ confidencePercent }}%</span>
        </header>

        <div v-if="problem" class="problem-editor">
          <label>
            <span>题目名称</span>
            <input v-model="problem.title" :disabled="busy" @change="persistEditedProblem" />
          </label>
          <label class="statement-field">
            <span>题目正文</span>
            <textarea v-model="problem.statement" :disabled="busy" spellcheck="false" @change="persistEditedProblem"></textarea>
          </label>
          <div class="problem-contract">
            <label><span>语言</span><select v-model="problem.language" :disabled="busy" @change="persistEditedProblem"><option value="python">Python</option><option value="javascript">JavaScript</option><option value="java">Java</option><option value="cpp">C++</option></select></label>
            <label><span>接口</span><select v-model="problem.interface_kind" :disabled="busy" @change="persistEditedProblem"><option value="function">函数题</option><option value="stdin_stdout">标准输入输出</option><option value="unknown">待确认</option></select></label>
          </div>
          <label v-if="problem.interface_kind === 'function'">
            <span>函数签名</span>
            <input v-model="problem.function_signature" :disabled="busy" placeholder="例如 twoSum(nums, target)" @change="persistEditedProblem" />
          </label>
          <ul v-if="problem.incomplete_reasons?.length" class="incomplete-list">
            <li v-for="reason in problem.incomplete_reasons" :key="reason">{{ reason }}</li>
          </ul>
        </div>
        <div v-else class="panel-loading"><i></i><p>正在读取当前视口题面…</p></div>

        <footer>
          <button type="button" class="quiet-button" :disabled="busy || !captureToken" @click="recapture(false)">重新识别</button>
          <button type="button" class="quiet-button" :disabled="busy || !captureToken" @click="recapture(true)">补充题面</button>
          <button v-if="problem?.incomplete_reasons?.length" type="button" class="primary-button" :disabled="!canSolve" @click="confirmProblemAndSolve">确认题面并解答</button>
        </footer>
      </article>

      <article class="assessment-panel solution-panel">
        <header>
          <div><span class="panel-index">答案</span><strong>{{ currentLanguage }}</strong></div>
          <div class="solution-actions">
            <button type="button" :disabled="!solution || busy" @click="generateSolution()">重新生成</button>
            <button type="button" :disabled="!solution" @click="copyCode">复制代码</button>
          </div>
        </header>

        <div v-if="solution" class="solution-content">
          <section class="approach-card">
            <span>解题思路</span>
            <p>{{ solution.approach_markdown }}</p>
            <div><code>{{ solution.time_complexity }}</code><code>{{ solution.space_complexity }}</code></div>
          </section>
          <label class="code-editor">
            <span>{{ currentLanguage }} · 可编辑</span>
            <textarea v-model="solution.code" :disabled="busy" spellcheck="false" @change="persistEditedSolution"></textarea>
          </label>
        </div>
        <div v-else class="code-skeleton" aria-live="polite">
          <i></i><i></i><i></i><i></i><i></i><i></i>
          <p>{{ phase === 'analyzing' ? '先判断输入输出与约束' : phase === 'generating' ? '正在组织思路并生成完整代码' : '题面确认后将在这里生成代码' }}</p>
        </div>
      </article>

      <article class="assessment-panel tests-panel">
        <header>
          <div><span class="panel-index">测试</span><strong>{{ testTotal ? `${passedTotal}/${testTotal}` : '待运行' }}</strong></div>
          <button type="button" :disabled="!solution || busy" @click="executeCurrent(null, false)">重新测试</button>
        </header>

        <div v-if="tests.length" class="test-list">
          <article v-for="(test, index) in tests" :key="test.test_id">
            <header>
              <span :class="`test-origin is-${test.kind}`">{{ test.kind === 'public' ? '公开样例' : 'AI 测试' }}</span>
              <strong>用例 {{ index + 1 }}</strong>
              <i :class="`result-${testResult(test.test_id)?.status || 'waiting'}`"></i>
            </header>
            <dl>
              <div><dt>输入</dt><dd>{{ typeof test.input_payload === 'string' ? test.input_payload : JSON.stringify(test.input_payload) }}</dd></div>
              <div><dt>期望</dt><dd>{{ test.expected_output }}</dd></div>
              <div v-if="testResult(test.test_id)"><dt>实际</dt><dd>{{ testResult(test.test_id).actual_output || testResult(test.test_id).error_summary }}</dd></div>
            </dl>
            <p v-if="test.explanation">{{ test.explanation }}</p>
          </article>
        </div>
        <div v-else class="test-empty">
          <span aria-hidden="true">▤</span>
          <p>公开样例与 AI 边界测试会在代码生成后自动运行。</p>
        </div>

        <footer>
          <div><span>本场题目</span><strong>{{ assessmentItems.length }}</strong></div>
          <button type="button" class="archive-button" :disabled="!solution || busy" @click="showArchive = true">保存到面经库</button>
          <button type="button" class="reset-button" :disabled="busy" @click="resetSession">结束本场</button>
        </footer>
      </article>
    </section>

    <div v-if="showArchive" class="archive-overlay" @click.self="showArchive = false">
      <form class="assessment-archive-dialog" @submit.prevent="archiveAssessment">
        <header><div><span>归档本场笔试</span><h2>保存到面经库</h2></div><button type="button" @click="showArchive = false">×</button></header>
        <p>将保存 {{ archivePayload().items.length }} 道题的确认题面、最终代码、思路和测试摘要；截图与页面原文不会入库。</p>
        <label><span>公司</span><input v-model.trim="archiveForm.company_name" required maxlength="120" placeholder="例如：字节跳动" /></label>
        <label><span>岗位</span><input v-model.trim="archiveForm.role_name" required maxlength="160" placeholder="例如：后端开发工程师" /></label>
        <label><span>笔试日期</span><input v-model="archiveForm.assessment_date" type="date" /></label>
        <footer><button type="button" @click="showArchive = false">取消</button><button type="submit" class="primary-button" :disabled="archiveBusy">{{ archiveBusy ? '保存中…' : '确认保存' }}</button></footer>
      </form>
    </div>
  </main>
</template>

<style scoped>
.assessment-page{--ink:#17233a;--muted:#6d7b91;--line:#dce3ed;--blue:#176fe5;--blue-deep:#0d4fa8;--blue-soft:#eaf3ff;--amber:#b56a08;--amber-soft:#fff5df;--green:#14745b;min-width:1180px;min-height:100vh;background:#eef2f7;color:var(--ink);font-family:"Microsoft YaHei","PingFang SC",sans-serif}.assessment-header{display:grid;grid-template-columns:minmax(260px,1fr) minmax(520px,720px) minmax(220px,1fr);height:72px;align-items:center;border-bottom:1px solid #d7e0eb;background:rgba(255,255,255,.96);padding:0 26px;box-shadow:0 4px 18px rgba(23,39,63,.05)}.assessment-brand{display:flex;align-items:center;gap:12px}.assessment-brand>span{display:grid;width:40px;height:40px;place-items:center;border-radius:12px 4px 12px 4px;background:var(--ink);color:#fff;font-family:Consolas,monospace;font-weight:900}.assessment-brand strong,.assessment-brand small{display:block}.assessment-brand strong{font-size:17px;letter-spacing:-.02em}.assessment-brand small{margin-top:3px;color:var(--muted);font-size:11px}.assessment-runway{display:grid;grid-template-columns:repeat(3,1fr);margin:0;padding:0;list-style:none}.assessment-runway li{position:relative;display:flex;align-items:center;justify-content:center;gap:8px;color:#8793a4;font-size:12px;font-weight:800}.assessment-runway li::after{position:absolute;z-index:0;top:50%;right:-20%;left:70%;height:1px;background:#d4deea;content:""}.assessment-runway li:last-child::after{display:none}.assessment-runway i{z-index:1;width:9px;height:9px;border:2px solid #a8b5c6;border-radius:50%;background:#fff}.assessment-runway .is-active{color:var(--blue)}.assessment-runway .is-active i{border-color:var(--blue);box-shadow:0 0 0 5px rgba(23,111,229,.12)}.assessment-runway .is-done{color:var(--green)}.assessment-runway .is-done i{border-color:var(--green);background:var(--green)}.assessment-header-status{display:flex;align-items:center;justify-content:flex-end;gap:10px}.assessment-header-status>span{border-radius:999px;background:#edf2f8;color:#53647d;padding:7px 11px;font-size:12px;font-weight:900}.assessment-header-status .phase-ready{background:#e6f5ef;color:var(--green)}.assessment-header-status .phase-partial,.assessment-header-status .phase-repairing{background:var(--amber-soft);color:var(--amber)}.assessment-header-status small{color:#8995a5;font:11px Bahnschrift,Arial,sans-serif}.assessment-notices{position:fixed;z-index:30;top:82px;right:22px;display:grid;width:min(480px,calc(100vw - 44px));gap:8px}.assessment-notices p{margin:0;border:1px solid;border-radius:10px;background:#fff;padding:11px 13px;box-shadow:0 12px 30px rgba(29,45,67,.13);font-size:12px;line-height:1.55}.assessment-notices .is-error{border-color:#f0c6c3;color:#a64541}.assessment-notices .is-notice{border-color:#bcd5f4;color:#1559aa}.assessment-empty{display:grid;width:min(720px,calc(100vw - 80px));min-height:calc(100vh - 72px);place-content:center;justify-items:center;margin:auto;text-align:center}.empty-glyph{display:grid;width:92px;height:92px;place-items:center;border:1px solid #cdd9e8;border-radius:30px 8px 30px 8px;background:#fff;box-shadow:0 18px 45px rgba(32,54,84,.1)}.empty-glyph span{color:var(--blue);font:42px Bahnschrift,Arial,sans-serif}.assessment-empty h1{margin:28px 0 10px;font-size:30px;letter-spacing:-.04em}.assessment-empty>p{max-width:610px;margin:0;color:var(--muted);line-height:1.8}.assessment-empty ol{display:flex;gap:12px;margin:28px 0 18px;padding:0;list-style:none}.assessment-empty li{display:flex;align-items:center;gap:8px;border:1px solid #d8e2ee;border-radius:12px;background:#fff;padding:10px 13px;color:#455874;font-size:12px}.assessment-empty li b{display:grid;width:20px;height:20px;place-items:center;border-radius:50%;background:var(--blue-soft);color:var(--blue);font:11px Bahnschrift,Arial,sans-serif}.assessment-empty small{color:#8995a5}.assessment-workbench{display:grid;height:calc(100vh - 72px);grid-template-columns:minmax(340px,34fr) minmax(480px,43fr) minmax(300px,23fr);gap:10px;padding:10px;overflow:hidden}.assessment-panel{display:flex;min-width:0;min-height:0;flex-direction:column;overflow:hidden;border:1px solid var(--line);border-radius:14px;background:#fff;box-shadow:0 8px 25px rgba(31,47,68,.045)}.assessment-panel>header{display:flex;min-height:54px;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid #e4e9f0;padding:0 16px}.assessment-panel>header>div:first-child{display:flex;align-items:center;gap:9px}.panel-index{border-radius:6px;background:#edf2f8;color:#66768c;padding:4px 6px;font:10px Bahnschrift,Arial,sans-serif;text-transform:uppercase}.assessment-panel>header strong{font-size:12px}.confidence{border-radius:999px;background:#e7f5ef;color:var(--green);padding:5px 8px;font-size:10px;font-weight:900}.confidence.low{background:var(--amber-soft);color:var(--amber)}.problem-editor{display:flex;min-height:0;flex:1;flex-direction:column;gap:13px;overflow:auto;padding:16px}.problem-editor label,.assessment-archive-dialog label{display:grid;gap:6px}.problem-editor label>span,.assessment-archive-dialog label>span{color:#6d7c91;font-size:11px;font-weight:800}.problem-editor input,.problem-editor select,.problem-editor textarea,.assessment-archive-dialog input{width:100%;border:1px solid #d8e1ec;border-radius:9px;background:#fbfcfe;color:var(--ink);padding:9px 10px;outline:none;font:12px inherit;box-sizing:border-box}.problem-editor input:focus,.problem-editor select:focus,.problem-editor textarea:focus,.assessment-archive-dialog input:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(23,111,229,.1)}.statement-field{min-height:240px;flex:1}.statement-field textarea{height:100%;min-height:240px;resize:none;line-height:1.75}.problem-contract{display:grid;grid-template-columns:1fr 1fr;gap:10px}.incomplete-list{display:grid;gap:5px;margin:0;border-left:3px solid #d08a24;background:var(--amber-soft);padding:10px 12px 10px 26px;color:#8b5a16;font-size:11px;line-height:1.5}.assessment-panel>footer{display:flex;align-items:center;gap:8px;border-top:1px solid #e4e9f0;padding:11px 14px}.assessment-panel button,.assessment-archive-dialog button{border:1px solid #d2dce8;border-radius:8px;background:#fff;color:#4d607b;padding:8px 10px;cursor:pointer;font:11px inherit;font-weight:850}.assessment-panel button:hover:not(:disabled),.assessment-archive-dialog button:hover:not(:disabled){border-color:#8db5e8;color:var(--blue-deep)}.assessment-panel button:disabled,.assessment-archive-dialog button:disabled{cursor:not-allowed;opacity:.45}.primary-button{border-color:var(--blue)!important;background:var(--blue)!important;color:#fff!important}.problem-panel>footer .primary-button{margin-left:auto}.panel-loading,.code-skeleton,.test-empty{display:grid;min-height:0;flex:1;place-content:center;justify-items:center;color:#8190a4;text-align:center}.panel-loading i{width:26px;height:26px;border:3px solid #d8e5f4;border-top-color:var(--blue);border-radius:50%;animation:spin .9s linear infinite}.solution-actions{display:flex;gap:7px}.solution-content{display:grid;min-height:0;flex:1;grid-template-rows:auto minmax(0,1fr);gap:10px;padding:12px}.approach-card{border:1px solid #dce7f4;border-radius:10px;background:#f6f9fd;padding:12px}.approach-card>span{color:var(--blue);font-size:10px;font-weight:900}.approach-card p{max-height:96px;margin:7px 0 10px;overflow:auto;color:#43546d;font-size:12px;line-height:1.65;white-space:pre-wrap}.approach-card>div{display:flex;gap:6px}.approach-card code{border-radius:5px;background:#e8eef6;color:#52627a;padding:4px 6px;font:10px Consolas,monospace}.code-editor{display:grid;min-height:0;grid-template-rows:auto minmax(0,1fr);gap:6px}.code-editor>span{color:#738198;font:10px Consolas,monospace}.code-editor textarea{width:100%;height:100%;min-height:0;resize:none;border:1px solid #1f2b3d;border-radius:10px;background:#111a28;color:#dbe7f5;padding:15px;outline:none;box-sizing:border-box;font:12px/1.65 Consolas,"SFMono-Regular",monospace;tab-size:4}.code-editor textarea:focus{border-color:#4d96f5;box-shadow:0 0 0 3px rgba(23,111,229,.15)}.code-skeleton{align-content:center;gap:8px;padding:30px}.code-skeleton i{display:block;width:78%;height:9px;border-radius:4px;background:linear-gradient(90deg,#edf1f6,#e1e8f1,#edf1f6);background-size:220% 100%;animation:shimmer 1.5s infinite}.code-skeleton i:nth-child(2n){width:62%}.code-skeleton p{margin:18px 0 0;font-size:12px}.tests-panel>header>button{padding:7px 9px}.test-list{display:grid;min-height:0;flex:1;align-content:start;gap:8px;overflow:auto;padding:12px}.test-list>article{border:1px solid #e0e7ef;border-radius:9px;background:#fbfcfe;padding:10px}.test-list article>header{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:7px}.test-origin{border-radius:5px;padding:3px 5px;font-size:9px;font-weight:900}.test-origin.is-public{background:#e8f1ff;color:#1765c8}.test-origin.is-generated{background:#f2ebff;color:#7349a7}.test-list article>header strong{font-size:11px}.test-list article>header i{width:8px;height:8px;border-radius:50%;background:#b6c1cf}.test-list .result-passed{background:#1e9a70}.test-list .result-failed,.test-list .result-timeout{background:#d6534d}.test-list dl{display:grid;gap:5px;margin:9px 0 0}.test-list dl>div{display:grid;grid-template-columns:36px minmax(0,1fr);gap:6px}.test-list dt{color:#8a97a7;font-size:9px}.test-list dd{margin:0;overflow-wrap:anywhere;color:#42536c;font:10px/1.45 Consolas,monospace;white-space:pre-wrap}.test-list article>p{margin:7px 0 0;color:#7f6a9e;font-size:9px;line-height:1.45}.test-empty span{color:#a7b5c7;font-size:30px}.test-empty p{max-width:220px;margin:12px 0 0;font-size:11px;line-height:1.6}.tests-panel>footer{display:grid;grid-template-columns:auto 1fr;gap:7px}.tests-panel>footer>div{display:flex;align-items:baseline;gap:5px}.tests-panel>footer>div span{color:#8491a3;font-size:9px}.tests-panel>footer>div strong{font:16px Bahnschrift,Arial,sans-serif}.archive-button{justify-self:end;border-color:#9fc0eb!important;color:var(--blue-deep)!important}.reset-button{grid-column:1/-1;color:#7a8798!important}.archive-overlay{position:fixed;z-index:80;inset:0;display:grid;place-items:center;background:rgba(15,26,42,.48);padding:30px}.assessment-archive-dialog{display:grid;width:min(520px,calc(100vw - 50px));gap:14px;border:1px solid #d7e1ed;border-radius:16px;background:#fff;padding:22px;box-shadow:0 30px 80px rgba(15,30,50,.28)}.assessment-archive-dialog>header{display:flex;align-items:flex-start;justify-content:space-between}.assessment-archive-dialog>header span{color:var(--blue);font-size:10px;font-weight:900}.assessment-archive-dialog h2{margin:4px 0 0;font-size:22px}.assessment-archive-dialog>header button{border:0;font-size:20px}.assessment-archive-dialog>p{margin:0;border-left:3px solid var(--blue);background:#f0f6ff;color:#53647b;padding:10px 12px;font-size:11px;line-height:1.6}.assessment-archive-dialog>footer{display:flex;justify-content:flex-end;gap:8px;border-top:1px solid #e6ebf1;padding-top:14px}@keyframes spin{to{transform:rotate(360deg)}}@keyframes shimmer{to{background-position:-220% 0}}@media(prefers-reduced-motion:reduce){.panel-loading i,.code-skeleton i{animation:none}}
.extension-recovery-actions{display:flex;align-items:center;justify-content:center;gap:10px;margin:26px 0 18px}.extension-recovery-actions a,.extension-recovery-actions button{border:1px solid var(--blue);border-radius:9px;background:var(--blue);color:#fff;padding:10px 14px;text-decoration:none;cursor:pointer;font:12px inherit;font-weight:850}.extension-recovery-actions a.secondary{border-color:#ccd8e7;background:#fff;color:#53647d}.extension-recovery-actions button{border-color:#9dbce2;background:#fff;color:var(--blue-deep)}.extension-recovery-actions button:disabled{cursor:wait;opacity:.55}
</style>
