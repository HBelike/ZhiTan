<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

defineProps({
  currentUser: { type: Object, default: null }
})

const records = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 16
const profiles = ref([])
const loading = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const mode = ref('home')
const detailRecord = ref(null)
const rightPane = ref('suggestions')
const workspacePane = ref('assistant')
const historyPane = ref('optimized')
const resumePreviewUrl = ref('')

const filters = ref({ jobTitle: '', startDate: '', endDate: '' })
const resumeFile = ref(null)
const screenshotFiles = ref([])
const jobDescription = ref('')
const extraPrompt = ref('')
const selectedProfileId = ref('')
const analysis = ref(null)
const selectedSuggestionIds = ref([])
const optimizedMarkdown = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const readyProfiles = computed(() => profiles.value.filter((item) => item.readiness === 'ready'))
const selectedSuggestions = computed(() => {
  const selected = new Set(selectedSuggestionIds.value)
  return (analysis.value?.suggestions ?? []).filter((item) => selected.has(item.id))
})

function resetMessages() {
  errorMessage.value = ''
  successMessage.value = ''
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail ?? `请求失败：${response.status}`)
  return payload
}

function normalizeProfileText(item) {
  const profile = item?.profile ?? {}
  return `${profile.profile_key ?? ''}${profile.display_name ?? ''}${profile.model_id ?? ''}`
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '')
}

async function loadProfiles() {
  const payload = await requestJson('/api/career/model-profiles')
  profiles.value = payload.items ?? []
  const preferred = readyProfiles.value.find((item) => normalizeProfileText(item).includes('deepseekv4flash'))
  const fallback = readyProfiles.value[0]
  selectedProfileId.value = (preferred?.profile?.id ?? fallback?.profile?.id ?? '')
}

async function loadRecords(targetPage = page.value) {
  loading.value = true
  resetMessages()
  try {
    const query = new URLSearchParams({ page: String(targetPage), page_size: String(pageSize) })
    if (filters.value.jobTitle.trim()) query.set('job_title', filters.value.jobTitle.trim())
    if (filters.value.startDate) query.set('start_date', filters.value.startDate)
    if (filters.value.endDate) query.set('end_date', filters.value.endDate)
    const payload = await requestJson(`/api/career/resume-optimizations?${query}`)
    records.value = payload.items ?? []
    total.value = Number(payload.total ?? 0)
    page.value = Number(payload.page ?? targetPage)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '简历优化历史读取失败'
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.value = { jobTitle: '', startDate: '', endDate: '' }
  void loadRecords(1)
}

function clearResumePreviewUrl() {
  if (resumePreviewUrl.value) {
    URL.revokeObjectURL(resumePreviewUrl.value)
    resumePreviewUrl.value = ''
  }
}

function resetWorkspace() {
  resumeFile.value = null
  screenshotFiles.value = []
  jobDescription.value = ''
  extraPrompt.value = ''
  analysis.value = null
  selectedSuggestionIds.value = []
  optimizedMarkdown.value = ''
  rightPane.value = 'suggestions'
  workspacePane.value = 'assistant'
  const fileInput = document.querySelector('#resume-file-input')
  if (fileInput) fileInput.value = ''
  const screenshotInput = document.querySelector('#resume-job-screenshots')
  if (screenshotInput) screenshotInput.value = ''
  clearResumePreviewUrl()
}

function openNewTask() {
  resetMessages()
  resetWorkspace()
  detailRecord.value = null
  mode.value = 'workspace'
  window.history.pushState({}, '', '/resume-assistant/new')
}

function returnHome({ replace = false } = {}) {
  resetMessages()
  mode.value = 'home'
  detailRecord.value = null
  const method = replace ? 'replaceState' : 'pushState'
  window.history[method]({}, '', '/resume-assistant')
  void loadRecords(1)
}

function onResumeSelected(event) {
  resumeFile.value = event.target.files?.[0] ?? null
  clearResumePreviewUrl()
  if (resumeFile.value) {
    resumePreviewUrl.value = URL.createObjectURL(resumeFile.value)
  }
}

function onScreenshotsSelected(event) {
  screenshotFiles.value = Array.from(event.target.files ?? [])
}

function isPdfResumeFile(file) {
  if (!file) return false
  return file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
}

async function analyzeResume() {
  resetMessages()
  if (!resumeFile.value) {
    errorMessage.value = '请先上传 PDF、DOC 或 DOCX 简历'
    return
  }
  if (!jobDescription.value.trim() && !screenshotFiles.value.length) {
    errorMessage.value = '请填写岗位介绍或上传岗位截图'
    return
  }
  if (!selectedProfileId.value) {
    errorMessage.value = '没有可用模型，请先在求职助手中配置 deepseek-v4-flash'
    return
  }
  loading.value = true
  try {
    const form = new FormData()
    form.append('resume_file', resumeFile.value)
    form.append('job_description', jobDescription.value)
    form.append('extra_prompt', extraPrompt.value)
    form.append('model_profile_id', selectedProfileId.value)
    screenshotFiles.value.forEach((file) => form.append('job_screenshots', file))
    analysis.value = await requestJson('/api/career/resume-optimizations/analyze', {
      method: 'POST',
      body: form
    })
    selectedSuggestionIds.value = (analysis.value.suggestions ?? []).map((item) => item.id)
    optimizedMarkdown.value = ''
    rightPane.value = 'suggestions'
    successMessage.value = '分析完成。请审核并调整修改意见。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '简历分析失败'
  } finally {
    loading.value = false
  }
}

function toggleAllSuggestions() {
  const all = analysis.value?.suggestions ?? []
  selectedSuggestionIds.value = selectedSuggestionIds.value.length === all.length
    ? []
    : all.map((item) => item.id)
}

async function generateResume() {
  resetMessages()
  if (!selectedSuggestions.value.length) {
    errorMessage.value = '请至少保留一条修改意见'
    return
  }
  loading.value = true
  try {
    const payload = await requestJson('/api/career/resume-optimizations/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        original_markdown: analysis.value.original_markdown,
        job_description_text: analysis.value.job_description_text,
        suggestions: selectedSuggestions.value,
        extra_prompt: extraPrompt.value,
        model_profile_id: analysis.value.model_profile_id
      })
    })
    optimizedMarkdown.value = payload.optimized_markdown ?? ''
    rightPane.value = 'optimized'
    successMessage.value = '优化简历已生成。确认内容后再保存到历史。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '优化简历生成失败'
  } finally {
    loading.value = false
  }
}

async function saveToHistory() {
  resetMessages()
  if (!optimizedMarkdown.value.trim() || !resumeFile.value || !analysis.value) return
  loading.value = true
  try {
    const form = new FormData()
    form.append('original_file', resumeFile.value)
    form.append('original_preview_markdown', analysis.value.original_markdown)
    form.append('job_title', analysis.value.job_title)
    form.append('job_description_text', analysis.value.job_description_text)
    form.append('extra_prompt', extraPrompt.value)
    form.append('suggestions_json', JSON.stringify(selectedSuggestions.value))
    form.append('optimized_markdown', optimizedMarkdown.value)
    form.append('model_profile_id', analysis.value.model_profile_id)
    const record = await requestJson('/api/career/resume-optimizations', { method: 'POST', body: form })
    detailRecord.value = record
    historyPane.value = 'optimized'
    mode.value = 'detail'
    window.history.replaceState({}, '', `/resume-assistant/${record.id}`)
    successMessage.value = '已保存到简历优化历史。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '历史保存失败'
  } finally {
    loading.value = false
  }
}

async function openRecord(recordId, { replace = false } = {}) {
  resetMessages()
  loading.value = true
  try {
    detailRecord.value = await requestJson(`/api/career/resume-optimizations/${recordId}`)
    historyPane.value = 'optimized'
    mode.value = 'detail'
    const method = replace ? 'replaceState' : 'pushState'
    window.history[method]({}, '', `/resume-assistant/${recordId}`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '简历优化记录读取失败'
  } finally {
    loading.value = false
  }
}

function downloadUrl(recordId, version) {
  return `/api/career/resume-optimizations/${recordId}/download/${version}`
}

function openRecordDownload(version) {
  const urls = detailRecord.value?.download_urls
  if (urls && typeof urls === 'object' && typeof urls[version] === 'string') {
    return urls[version]
  }
  if (!detailRecord.value?.id) return '#'
  return downloadUrl(detailRecord.value.id, version)
}

function formatDate(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
  }).format(new Date(value))
}

function handlePopState() {
  void restoreRoute()
}

async function restoreRoute() {
  const match = window.location.pathname.match(/^\/resume-assistant\/([0-9a-f-]{36})$/i)
  if (match) {
    await openRecord(match[1], { replace: true })
    return
  }
  if (window.location.pathname === '/resume-assistant/new') {
    mode.value = 'workspace'
    return
  }
  mode.value = 'home'
  await loadRecords()
}

onMounted(async () => {
  window.addEventListener('popstate', handlePopState)
  loading.value = true
  try {
    await loadProfiles()
    await restoreRoute()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '简历助手初始化失败'
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('popstate', handlePopState)
  clearResumePreviewUrl()
})
</script>

<template>
  <section
    class="resume-assistant"
    :class="[`show-workspace-${workspacePane}`, `show-history-${historyPane}`]"
  >
    <div v-if="errorMessage" class="resume-alert error">{{ errorMessage }}</div>
    <div v-if="successMessage" class="resume-alert success">{{ successMessage }}</div>

    <template v-if="mode === 'home'">
      <header class="resume-page-heading">
        <div>
          <h2>简历优化记录</h2>
          <p>按目标岗位查找已保存的优化结果</p>
        </div>
        <button type="button" class="primary-button" @click="openNewTask">＋ 新建优化任务</button>
      </header>

      <form class="resume-search" @submit.prevent="loadRecords(1)">
        <label class="search-keyword">
          <span>岗位名称</span>
          <input v-model="filters.jobTitle" type="search" placeholder="搜索目标岗位" />
        </label>
        <label>
          <span>开始日期</span>
          <input v-model="filters.startDate" type="date" />
        </label>
        <label>
          <span>结束日期</span>
          <input v-model="filters.endDate" type="date" />
        </label>
        <div class="search-actions">
          <button type="submit" class="primary-button compact">搜索</button>
          <button type="button" class="secondary-button compact" @click="resetFilters">重置</button>
        </div>
      </form>

      <div class="record-summary">
        <strong>历史记录</strong>
        <span>{{ total }} 条</span>
      </div>
      <div v-if="loading && !records.length" class="empty-state">正在读取记录…</div>
      <div v-else-if="!records.length" class="empty-state">
        <strong>还没有保存过优化简历</strong>
        <span>新建任务并确认保存后，记录会显示在这里。</span>
      </div>
      <div v-else class="resume-card-grid">
        <button
          v-for="record in records"
          :key="record.id"
          type="button"
          class="resume-history-card"
          @click="openRecord(record.id)"
        >
          <strong>{{ record.job_title }}</strong>
          <dl>
            <div><dt>创建人</dt><dd>{{ record.creator_name }}</dd></div>
            <div><dt>创建时间</dt><dd>{{ formatDate(record.created_at) }}</dd></div>
          </dl>
        </button>
      </div>
      <nav v-if="totalPages > 1" class="pagination" aria-label="简历优化记录分页">
        <button type="button" :disabled="page <= 1" @click="loadRecords(page - 1)">上一页</button>
        <span>{{ page }} / {{ totalPages }}</span>
        <button type="button" :disabled="page >= totalPages" @click="loadRecords(page + 1)">下一页</button>
      </nav>
    </template>

    <template v-else-if="mode === 'workspace'">
      <header class="canvas-heading">
        <button type="button" class="back-button" @click="returnHome()">← 返回历史</button>
        <div>
          <h2>新建简历优化</h2>
          <p>先审核 AI 建议，确认后生成新简历，最后由你决定是否保存。</p>
        </div>
      </header>

      <section class="task-input-panel">
        <label class="file-field">
          <span>原始简历</span>
          <input id="resume-file-input" type="file" accept=".pdf,.doc,.docx" @change="onResumeSelected" />
          <small>{{ resumeFile?.name || '支持 PDF、DOC、DOCX' }}</small>
        </label>
        <label class="model-field">
          <span>分析模型</span>
          <select v-model="selectedProfileId">
            <option v-for="item in readyProfiles" :key="item.profile.id" :value="item.profile.id">
              {{ item.profile.display_name }} · {{ item.profile.model_id }}
            </option>
          </select>
        </label>
        <label class="job-copy-field">
          <span>岗位介绍</span>
          <textarea v-model="jobDescription" rows="5" placeholder="粘贴岗位职责、任职要求和加分项"></textarea>
        </label>
        <label class="screenshot-field">
          <span>岗位截图</span>
          <input id="resume-job-screenshots" type="file" accept="image/*" multiple @change="onScreenshotsSelected" />
          <small>{{ screenshotFiles.length ? `已选择 ${screenshotFiles.length} 张` : '可多选，按上传顺序解析' }}</small>
        </label>
        <label class="extra-prompt-field">
          <span>额外要求</span>
          <textarea v-model="extraPrompt" rows="3" placeholder="例如：突出 Agent 工程经验，措辞克制，不增加原简历没有的指标"></textarea>
        </label>
        <div class="input-actions">
          <button type="button" class="primary-button" :disabled="loading" @click="analyzeResume">
            {{ loading ? '分析中…' : analysis ? '重新分析' : '开始分析' }}
          </button>
        </div>
      </section>

      <div v-if="analysis" class="resume-pane-switcher" role="tablist" aria-label="简历工作区">
        <button
          type="button"
          role="tab"
          aria-controls="resume-document-pane"
          :aria-selected="workspacePane === 'document'"
          :class="{ active: workspacePane === 'document' }"
          @click="workspacePane = 'document'"
        >原始简历</button>
        <button
          type="button"
          role="tab"
          aria-controls="resume-assistant-pane"
          :aria-selected="workspacePane === 'assistant'"
          :class="{ active: workspacePane === 'assistant' }"
          @click="workspacePane = 'assistant'"
        >优化助手</button>
      </div>

      <section v-if="analysis" class="resume-work-canvas">
        <article id="resume-document-pane" class="document-pane" role="tabpanel">
          <header>
            <div><span>原始简历</span><strong>{{ resumeFile?.name }}</strong></div>
          </header>
          <iframe
            v-if="isPdfResumeFile(resumeFile)"
            class="pdf-preview"
            :src="resumePreviewUrl"
          ></iframe>
          <div v-else class="text-preview-wrap">
            <pre>{{ analysis.original_markdown }}</pre>
          </div>
        </article>

        <article id="resume-assistant-pane" class="assistant-pane" role="tabpanel">
          <header>
            <div>
              <span>简历优化助手</span>
              <strong>{{ analysis.job_title }}</strong>
            </div>
            <div v-if="optimizedMarkdown" class="pane-tabs" role="tablist" aria-label="优化内容">
              <button type="button" role="tab" :aria-selected="rightPane === 'suggestions'" :class="{ active: rightPane === 'suggestions' }" @click="rightPane = 'suggestions'">修改意见</button>
              <button type="button" role="tab" :aria-selected="rightPane === 'optimized'" :class="{ active: rightPane === 'optimized' }" @click="rightPane = 'optimized'">优化简历</button>
            </div>
          </header>

          <template v-if="rightPane === 'suggestions'">
            <div class="analysis-summary">{{ analysis.analysis_summary }}</div>
            <div class="suggestion-toolbar">
              <span>已选择 {{ selectedSuggestions.length }} / {{ analysis.suggestions.length }}</span>
              <button type="button" @click="toggleAllSuggestions">全选 / 清空</button>
            </div>
            <div class="suggestion-list">
              <label v-for="item in analysis.suggestions" :key="item.id" class="suggestion-card">
                <input v-model="selectedSuggestionIds" type="checkbox" :value="item.id" />
                <div>
                  <div class="suggestion-title">
                    <strong>{{ item.title }}</strong>
                    <span :class="`priority-${item.priority}`">{{ item.priority }}</span>
                  </div>
                  <p>{{ item.rationale }}</p>
                  <small>简历依据：{{ item.original_evidence }}</small>
                  <small>岗位依据：{{ item.job_evidence }}</small>
                  <textarea v-model="item.proposed_change" rows="3" aria-label="编辑修改建议"></textarea>
                </div>
              </label>
            </div>
            <footer>
              <button type="button" class="secondary-button" :disabled="loading" @click="analyzeResume">重新分析</button>
              <button type="button" class="primary-button" :disabled="loading || !selectedSuggestions.length" @click="generateResume">
                {{ loading ? '生成中…' : '生成优化简历' }}
              </button>
            </footer>
          </template>

          <template v-else>
            <div class="optimized-preview">
              <pre>{{ optimizedMarkdown }}</pre>
            </div>
            <footer>
              <button type="button" class="secondary-button" @click="rightPane = 'suggestions'">返回修改意见</button>
              <button type="button" class="primary-button" :disabled="loading" @click="saveToHistory">
                {{ loading ? '保存中…' : '保存到历史' }}
              </button>
            </footer>
          </template>
        </article>
      </section>
    </template>

    <template v-else-if="mode === 'detail' && detailRecord">
      <header class="canvas-heading detail-heading">
        <button type="button" class="back-button" @click="returnHome()">← 返回历史</button>
        <div>
          <h2>{{ detailRecord.job_title }}</h2>
          <p>{{ detailRecord.creator_name }} · {{ formatDate(detailRecord.created_at) }} · {{ detailRecord.model_display_name }}</p>
        </div>
      </header>
      <div class="resume-pane-switcher" role="tablist" aria-label="历史简历版本">
        <button
          type="button"
          role="tab"
          aria-controls="history-original-pane"
          :aria-selected="historyPane === 'original'"
          :class="{ active: historyPane === 'original' }"
          @click="historyPane = 'original'"
        >原始简历</button>
        <button
          type="button"
          role="tab"
          aria-controls="history-optimized-pane"
          :aria-selected="historyPane === 'optimized'"
          :class="{ active: historyPane === 'optimized' }"
          @click="historyPane = 'optimized'"
        >优化后简历</button>
      </div>
      <section class="history-document-grid">
        <article id="history-original-pane" class="document-pane history-original-pane" role="tabpanel">
          <header>
            <div><span>原始简历</span><strong>{{ detailRecord.source_filename }}</strong></div>
            <div class="document-actions">
              <a :href="openRecordDownload('original')" target="_blank" rel="noreferrer">下载原文件</a>
              <a :href="openRecordDownload('original-preview')" target="_blank" rel="noreferrer">下载原始预览PDF</a>
            </div>
          </header>
          <iframe class="pdf-preview" :src="openRecordDownload('original-preview')"></iframe>
        </article>
        <article id="history-optimized-pane" class="document-pane optimized-pane history-optimized-pane" role="tabpanel">
          <header>
            <div><span>优化后简历</span><strong>导出文件</strong></div>
            <div class="document-actions">
              <a :href="openRecordDownload('optimized-docx')" target="_blank" rel="noreferrer">下载 DOCX</a>
              <a :href="openRecordDownload('optimized-pdf')" target="_blank" rel="noreferrer">下载 PDF</a>
            </div>
          </header>
          <iframe class="pdf-preview" :src="openRecordDownload('optimized-pdf')"></iframe>
        </article>
      </section>
    </template>
  </section>
</template>

<style scoped>
.resume-assistant { min-width: 0; min-height: 0; container: resume-assistant / inline-size; color: #1e2c21; }
.resume-alert { margin: 0 0 14px; padding: 12px 16px; border-radius: 10px; font-size: 14px; }
.resume-alert.error { color: #a13d37; background: #fff0ee; border: 1px solid #efcbc7; }
.resume-alert.success { color: #4f711d; background: #f1f8df; border: 1px solid #d8e8b5; }
.resume-page-heading, .canvas-heading { display: flex; align-items: center; justify-content: space-between; gap: 24px; margin: 4px 0 22px; }
.resume-page-heading h2, .canvas-heading h2 { margin: 0; font-size: 26px; letter-spacing: -.02em; }
.resume-page-heading p, .canvas-heading p { margin: 5px 0 0; color: #81907f; font-size: 13px; }
button, input, select, textarea { font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .55; }
.primary-button, .secondary-button, .back-button { min-height: 40px; border-radius: 10px; padding: 0 18px; font-weight: 700; }
.primary-button { color: #fff; background: #86ad32; border: 1px solid #86ad32; }
.primary-button:hover:not(:disabled) { background: #769b28; }
.secondary-button, .back-button { color: #536548; background: #fff; border: 1px solid #dbe4d3; }
.compact { min-height: 42px; }
.resume-search { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); align-items: end; gap: 14px; padding: 18px; background: #fff; border: 1px solid #e1e8dc; border-radius: 14px; }
.resume-search label, .task-input-panel label { display: grid; gap: 7px; color: #42523f; font-size: 12px; font-weight: 700; }
.resume-search input, .task-input-panel input, .task-input-panel select, .task-input-panel textarea { width: 100%; box-sizing: border-box; border: 1px solid #dce5d5; border-radius: 9px; background: #fbfcf9; color: #243324; outline: none; }
.resume-search input, .task-input-panel input, .task-input-panel select { min-height: 42px; padding: 0 13px; }
.task-input-panel textarea { padding: 12px 13px; resize: vertical; }
.resume-search input:focus, .task-input-panel input:focus, .task-input-panel select:focus, .task-input-panel textarea:focus { border-color: #94b74c; box-shadow: 0 0 0 3px rgba(139, 174, 55, .11); }
.search-actions { display: flex; gap: 8px; }
.record-summary { display: flex; justify-content: space-between; align-items: baseline; margin: 24px 0 12px; }
.record-summary strong { font-size: 19px; }
.record-summary span { color: #8a9785; font-size: 13px; }
.resume-card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
.resume-history-card { min-height: 190px; padding: 22px; text-align: left; background: #fff; border: 1px solid #dfe7d8; border-radius: 14px; color: #203020; transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease; }
.resume-history-card:hover { transform: translateY(-2px); border-color: #a7c36a; box-shadow: 0 12px 30px rgba(57, 81, 41, .08); }
.resume-history-card > strong { display: block; min-height: 58px; font-size: 18px; line-height: 1.55; }
.resume-history-card dl { margin: 32px 0 0; border-top: 1px solid #edf1e9; }
.resume-history-card dl div { display: flex; justify-content: space-between; gap: 14px; padding-top: 12px; }
.resume-history-card dt { color: #8b9886; }
.resume-history-card dd { margin: 0; font-weight: 650; text-align: right; }
.empty-state { min-height: 240px; display: grid; place-content: center; gap: 8px; text-align: center; color: #879482; background: #fff; border: 1px dashed #d6e1ce; border-radius: 14px; }
.empty-state strong { color: #40513c; font-size: 17px; }
.pagination { display: flex; justify-content: center; align-items: center; gap: 14px; margin: 24px 0; }
.pagination button { border: 1px solid #dbe4d3; border-radius: 8px; background: #fff; padding: 8px 14px; }
.task-input-panel { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px 18px; padding: 20px; margin-bottom: 18px; background: #fff; border: 1px solid #dfe7d8; border-radius: 14px; }
.job-copy-field { grid-column: 1 / 2; }
.screenshot-field { grid-column: 2 / 3; align-content: start; }
.extra-prompt-field { grid-column: 1 / -1; }
.file-field small, .screenshot-field small { color: #879482; font-weight: 400; }
.input-actions { grid-column: 1 / -1; display: flex; justify-content: flex-end; }
.canvas-heading { justify-content: flex-start; }
.canvas-heading > div { flex: 1; }
.resume-pane-switcher { display:none; grid-template-columns:repeat(2,minmax(0,1fr)); gap:4px; margin:0 0 12px; border:1px solid #dfe7d8; border-radius:11px; background:#f1f4ed; padding:4px; }
.resume-pane-switcher button { min-height:40px; border:0; border-radius:8px; background:transparent; color:#65745f; font-weight:750; }
.resume-pane-switcher button.active { background:#fff; color:#425d19; box-shadow:0 2px 7px rgba(40,58,30,.1); }
.resume-work-canvas, .history-document-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); height: clamp(520px, calc(100dvh - 310px), 720px); min-height: 0; gap: 16px; }
.document-pane, .assistant-pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; background: #fff; border: 1px solid #dfe7d8; border-radius: 14px; }
.document-pane > header, .assistant-pane > header { min-height: 66px; padding: 14px 16px; display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid #e7ece3; }
.document-pane header div:first-child, .assistant-pane header > div:first-child { display: grid; gap: 3px; }
.document-pane header span, .assistant-pane header span { color: #81907f; font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }
.document-pane header button, .assistant-pane header button, .document-actions a { border: 0; background: transparent; color: #668526; font-weight: 700; text-decoration: none; }
.document-pane pre { flex: 1; margin: 0; padding: 22px; overflow: auto; white-space: pre-wrap; word-break: break-word; font: 14px/1.8 ui-monospace, SFMono-Regular, Consolas, monospace; color: #334031; background: #fcfdfa; }
.text-preview-wrap { flex: 1; overflow: auto; min-height: 0; background: #fcfdfa; }
.text-preview-wrap pre { margin: 0; min-height: 0; padding: 22px; white-space: pre-wrap; word-break: break-word; font: 14px/1.8 ui-monospace, SFMono-Regular, Consolas, monospace; color: #334031; }
.pdf-preview { width: 100%; flex: 1; border: 0; background: #fff; }
.analysis-summary { margin: 16px; padding: 14px; border-left: 3px solid #90b540; background: #f5f9e9; line-height: 1.7; }
.suggestion-toolbar { display: flex; justify-content: space-between; padding: 0 16px 10px; color: #71806d; font-size: 12px; }
.suggestion-toolbar button { border: 0; background: transparent; color: #668526; font-weight: 700; }
.suggestion-list { flex: 1; overflow: auto; padding: 0 16px 16px; }
.suggestion-card { display: grid; grid-template-columns: 20px 1fr; gap: 10px; padding: 14px 0; border-top: 1px solid #e9eee5; }
.suggestion-card input { margin-top: 4px; accent-color: #86ad32; }
.suggestion-title { display: flex; justify-content: space-between; gap: 12px; }
.suggestion-title span { padding: 2px 7px; border-radius: 999px; background: #edf4df; color: #678329; font-size: 10px; }
.suggestion-card p { margin: 7px 0; line-height: 1.6; }
.suggestion-card small { display: block; color: #7f8d7b; line-height: 1.6; }
.suggestion-card textarea { width: 100%; box-sizing: border-box; margin-top: 9px; padding: 10px; resize: vertical; border: 1px solid #dce5d5; border-radius: 8px; color: #334031; background: #fbfcf9; }
.assistant-pane footer { display: flex; justify-content: flex-end; gap: 9px; padding: 14px 16px; border-top: 1px solid #e6ece1; }
.pane-tabs { display: flex; gap: 3px; padding: 3px; border-radius: 8px; background: #f1f4ed; }
.pane-tabs button { padding: 7px 10px; border-radius: 6px; }
.pane-tabs button.active { background: #fff; color: #425d19; box-shadow: 0 1px 3px rgba(40, 58, 30, .1); }
.optimized-preview { flex: 1; min-height: 0; padding: 22px; overflow: auto; background: #fcfdfa; }
.optimized-preview pre { margin: 0; white-space: pre-wrap; word-break: break-word; font: 14px/1.8 ui-monospace, SFMono-Regular, Consolas, monospace; color: #334031; }
.detail-heading { margin-bottom: 18px; }
.history-document-grid { height: clamp(520px, calc(100dvh - 185px), 860px); }
.document-actions { display: flex !important; grid-auto-flow: column; gap: 12px !important; }

@container resume-assistant (max-width:1100px) {
  .resume-search {
    grid-template-columns:repeat(2,minmax(0,1fr));
  }

  .search-actions {
    grid-column:1 / -1;
    justify-content:flex-end;
  }

  .resume-work-canvas,
  .history-document-grid {
    grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);
  }
}

@container resume-assistant (max-width:900px) {
  .resume-page-heading,
  .canvas-heading {
    align-items:flex-start;
    flex-direction:column;
    gap:12px;
  }

  .resume-page-heading .primary-button,
  .canvas-heading .back-button {
    width:100%;
  }

  .resume-search,
  .task-input-panel {
    grid-template-columns:minmax(0,1fr);
    padding:14px;
  }

  .resume-search > *,
  .task-input-panel > *,
  .job-copy-field,
  .screenshot-field,
  .extra-prompt-field,
  .input-actions,
  .search-actions {
    min-width:0;
    grid-column:1;
  }

  .search-actions,
  .input-actions {
    align-items:stretch;
    flex-direction:column;
  }

  .search-actions button,
  .input-actions button {
    width:100%;
  }

  .resume-pane-switcher {
    display:grid;
  }

  .resume-work-canvas,
  .history-document-grid {
    height:clamp(420px,calc(100dvh - 160px),720px);
    grid-template-columns:minmax(0,1fr);
    grid-template-rows:minmax(0,1fr);
  }

  .resume-work-canvas > .document-pane,
  .resume-work-canvas > .assistant-pane,
  .history-document-grid > .document-pane {
    min-width:0;
    grid-row:1;
    grid-column:1;
    display:none;
  }

  .show-workspace-document .resume-work-canvas > .document-pane,
  .show-workspace-assistant .resume-work-canvas > .assistant-pane,
  .show-history-original .history-document-grid > .history-original-pane,
  .show-history-optimized .history-document-grid > .history-optimized-pane {
    display:flex;
  }

  .document-pane > header,
  .assistant-pane > header {
    align-items:flex-start;
    flex-direction:column;
  }

  .document-actions {
    display:flex !important;
    flex-wrap:wrap;
    grid-auto-flow:row;
  }
}
</style>
