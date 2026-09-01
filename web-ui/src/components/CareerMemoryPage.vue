<script setup>
import { computed, onMounted, ref } from 'vue'
import { CAREER_MEMORY_LABELS, CAREER_MEMORY_TYPES, groupCareerMemories, memorySourceLabel } from '../career-memory-view.js'

const emit = defineEmits(['navigate'])
const spaces = ref([])
const selectedSpaceId = ref('')
const memories = ref([])
const activeType = ref(CAREER_MEMORY_TYPES[0])
const loading = ref(false)
const importingResumes = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const editorMode = ref('')
const editingItem = ref(null)
const editorText = ref('')
const grouped = computed(() => groupCareerMemories(memories.value))
const totalMemoryCount = computed(() => memories.value.filter((item) => item.status !== 'superseded').length)
const activeTypeLabel = computed(() => CAREER_MEMORY_LABELS[activeType.value])
const SPACE_STORAGE_KEY = 'career-selected-space-id'

const CATEGORY_DESCRIPTIONS = {
  job_intention: '你希望应聘的岗位、行业或地点，由你直接维护。',
  work_experience: '主要来自已确认简历中的任职和实习经历。',
  education: '主要来自已确认简历中的学校、专业和学历。',
  award: '主要来自已确认简历中的竞赛、荣誉和奖项。',
  publication: '主要来自已确认简历中的论文和发表成果。',
  personal_advantage: '主要来自已确认简历中可被事实支撑的技能和优势。'
}

function visibleMemories(type) {
  const collection = grouped.value[type]
  return [...collection.candidate, ...collection.active, ...collection.disabled]
}

function memoryStatusLabel(status) {
  if (status === 'candidate') return '待确认'
  if (status === 'disabled') return '已停用'
  return '已启用'
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail?.message || payload.detail || `请求失败：${response.status}`)
  return payload
}

async function loadSpaces() {
  const payload = await requestJson('/api/career/career-spaces')
  spaces.value = payload.items || []
  selectedSpaceId.value ||= spaces.value.find((item) => item.is_default)?.id || spaces.value[0]?.id || ''
  const saved = window.localStorage.getItem(SPACE_STORAGE_KEY)
  if (spaces.value.some((item) => item.id === saved)) selectedSpaceId.value = saved
}

async function loadMemories({ silent = false } = {}) {
  if (!selectedSpaceId.value) return
  window.localStorage.setItem(SPACE_STORAGE_KEY, selectedSpaceId.value)
  if (!silent) loading.value = true
  error.value = ''
  try {
    const payload = await requestJson(`/api/career/memories?career_space_id=${encodeURIComponent(selectedSpaceId.value)}`)
    memories.value = payload.items || []
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '读取求职记忆失败'
  } finally {
    if (!silent) loading.value = false
  }
}

function openCreate(type = activeType.value) {
  activeType.value = type
  editingItem.value = null
  editorMode.value = 'create'
  editorText.value = ''
  notice.value = ''
}

function openEdit(item) {
  activeType.value = item.memory_type
  editingItem.value = item
  editorMode.value = 'edit'
  editorText.value = item.display_text
  notice.value = ''
}

function closeEditor() {
  editorMode.value = ''
  editingItem.value = null
  editorText.value = ''
}

async function saveEditor() {
  const text = editorText.value.trim()
  if (!text || saving.value) return
  saving.value = true
  error.value = ''
  try {
    if (editorMode.value === 'create') {
      await requestJson('/api/career/memories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          memory_type: activeType.value,
          career_space_id: selectedSpaceId.value,
          display_text: text
        })
      })
      notice.value = `已新增一条${activeTypeLabel.value}`
    } else {
      const normalizedKey = editingItem.value.memory_type === 'job_intention' ? 'statement' : 'summary'
      await requestJson(`/api/career/memories/${editingItem.value.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_text: text, normalized_value: { [normalizedKey]: text } })
      })
      notice.value = '修改已保存，并会在后续相关对话中生效'
    }
    closeEditor()
    await loadMemories({ silent: true })
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function operate(item, action) {
  if (action === 'delete' && !window.confirm('永久删除这条求职记忆？删除后不会再用于回答。')) return
  let url = `/api/career/memories/${item.id}`
  const options = { method: action === 'delete' ? 'DELETE' : 'POST' }
  if (action === 'confirm') url += '/confirm'
  if (action === 'disable') url += '/disable'
  try {
    await requestJson(url, options)
    notice.value = action === 'delete' ? '信息已删除' : '状态已更新'
    await loadMemories({ silent: true })
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '操作失败'
  }
}

async function importExistingResumes() {
  if (importingResumes.value) return
  importingResumes.value = true
  error.value = ''
  notice.value = ''
  try {
    const payload = await requestJson('/api/career/memory-imports/resumes', { method: 'POST' })
    notice.value = payload.message
    if (payload.queued_profile_count) {
      const previousCount = memories.value.length
      for (let attempt = 0; attempt < 8; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500))
        await loadMemories({ silent: true })
        if (memories.value.length !== previousCount) break
      }
      notice.value = '简历信息已提交整理；如内容较多，稍后刷新即可看到完整结果'
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '简历信息生成失败'
  } finally {
    importingResumes.value = false
  }
}

onMounted(async () => {
  try {
    await loadSpaces()
    await loadMemories()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '页面加载失败'
    loading.value = false
  }
})
</script>

<template>
  <main class="career-memory-page">
    <div class="memory-page-shell">
      <header class="memory-page-header">
        <div class="memory-page-heading">
          <button type="button" class="back-button" @click="emit('navigate', '/career')">← 返回求职助手</button>
          <h1>我的求职记忆</h1>
          <p>管理求职助手可以跨对话复用的个人信息。修改的是提供给模型的事实，不是系统提示词。</p>
        </div>
        <div class="memory-header-actions">
          <label class="memory-space-field">
            <span>职业空间</span>
            <select v-model="selectedSpaceId" @change="loadMemories()">
              <option v-for="space in spaces" :key="space.id" :value="space.id">{{ space.name }}</option>
            </select>
          </label>
          <button type="button" class="primary-button" @click="openCreate()">＋ 新增信息</button>
        </div>
      </header>

      <section class="memory-sync-strip" aria-label="简历同步说明">
        <div class="sync-mark" aria-hidden="true">简</div>
        <div class="sync-copy">
          <strong>简历信息会自动同步</strong>
          <p>上传或更新同名简历后，工作经历、学历、奖项、论文和个人优势会自动刷新；只把已启用的相关信息提供给模型。</p>
        </div>
        <button type="button" class="secondary-button" :disabled="importingResumes" @click="importExistingResumes">
          {{ importingResumes ? '正在整理…' : '从已有简历生成' }}
        </button>
      </section>

      <section v-if="error" class="memory-error" role="alert">
        <div><strong>操作没有完成</strong><p>{{ error }}</p></div>
        <button type="button" @click="loadMemories()">重新读取</button>
      </section>
      <p v-if="notice" class="memory-notice" aria-live="polite">{{ notice }}</p>

      <section v-if="loading" class="memory-loading" aria-live="polite"><span></span>正在读取求职记忆…</section>
      <section v-else class="memory-content">
        <header class="memory-content-header">
          <div>
            <h2>已保存的信息</h2>
            <p>共 {{ totalMemoryCount }} 条，可按分类直接新增、修正、停用或删除。</p>
          </div>
          <div class="memory-status-key" aria-label="状态说明">
            <span class="active"><i></i>已启用</span>
            <span class="candidate"><i></i>待确认</span>
            <span class="disabled"><i></i>已停用</span>
          </div>
        </header>

        <div class="memory-card-grid">
          <article v-for="type in CAREER_MEMORY_TYPES" :key="type" class="memory-group-card">
            <header class="memory-group-header">
              <div class="memory-type-mark" aria-hidden="true">{{ CAREER_MEMORY_LABELS[type].slice(0, 1) }}</div>
              <div>
                <h3>{{ CAREER_MEMORY_LABELS[type] }}</h3>
                <p>{{ CATEGORY_DESCRIPTIONS[type] }}</p>
              </div>
              <span>{{ visibleMemories(type).length }} 条</span>
            </header>

            <form v-if="editorMode && activeType === type" class="memory-editor" @submit.prevent="saveEditor">
              <label>{{ editorMode === 'create' ? `新增${CAREER_MEMORY_LABELS[type]}` : `修正${CAREER_MEMORY_LABELS[type]}` }}</label>
              <textarea
                v-model="editorText"
                maxlength="500"
                rows="3"
                autofocus
                :placeholder="`写下明确、真实的${CAREER_MEMORY_LABELS[type]}`"
              ></textarea>
              <footer>
                <small>{{ editorText.length }}/500</small>
                <button type="button" class="text-button" @click="closeEditor">取消</button>
                <button type="submit" class="primary-button compact" :disabled="!editorText.trim() || saving">{{ saving ? '保存中…' : '保存并启用' }}</button>
              </footer>
            </form>

            <div
              v-if="!visibleMemories(type).length && !(editorMode && activeType === type)"
              class="memory-group-empty"
            >
              <p>暂无{{ CAREER_MEMORY_LABELS[type] }}</p>
              <button type="button" @click="openCreate(type)">＋ 新增</button>
            </div>

            <div v-else class="memory-group-items">
              <article v-for="item in visibleMemories(type)" :key="item.id" class="memory-item" :class="`status-${item.status}`">
                <div class="memory-item-meta">
                  <span :class="item.status">{{ memoryStatusLabel(item.status) }}</span>
                  <small>{{ memorySourceLabel(item) }}</small>
                </div>
                <p>{{ item.display_text }}</p>
                <footer class="memory-card-actions">
                  <button v-if="item.status === 'candidate'" type="button" class="primary" @click="operate(item, 'confirm')">确认并启用</button>
                  <button v-if="item.status !== 'disabled'" type="button" @click="openEdit(item)">修正</button>
                  <button v-if="item.status === 'active'" type="button" @click="operate(item, 'disable')">停用</button>
                  <button type="button" class="danger" @click="operate(item, 'delete')">删除</button>
                </footer>
              </article>
            </div>

            <button
              v-if="visibleMemories(type).length && !(editorMode && activeType === type)"
              type="button"
              class="memory-card-add"
              @click="openCreate(type)"
            >＋ 新增{{ CAREER_MEMORY_LABELS[type] }}</button>
          </article>
        </div>
      </section>
    </div>
  </main>
</template>

<style scoped>
.career-memory-page {
  min-height: 100%;
  box-sizing: border-box;
  background: var(--ui-canvas);
  color: var(--ui-text);
  padding: clamp(24px, 2vw, 38px) clamp(24px, 2.6vw, 52px) 52px;
}

.memory-page-shell {
  width: min(1640px, 100%);
  margin: 0 auto;
}

button,
select,
textarea {
  font: inherit;
}

.memory-page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 32px;
}

.memory-page-heading {
  max-width: 760px;
}

.back-button {
  border: 0;
  background: transparent;
  color: var(--ui-accent-ink);
  padding: 0;
  font-size: 13px;
  font-weight: 800;
  cursor: pointer;
}

.memory-page-heading h1 {
  margin: 13px 0 7px;
  font: 850 32px/1.18 var(--ui-font-display);
  letter-spacing: -.03em;
}

.memory-page-heading p {
  margin: 0;
  color: var(--ui-text-muted);
  font-size: 13px;
  line-height: 1.7;
}

.memory-header-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: flex-end;
  gap: 10px;
}

.memory-space-field {
  display: grid;
  gap: 6px;
  color: var(--ui-text-secondary);
  font-size: 11px;
  font-weight: 800;
}

.memory-space-field select {
  min-width: 220px;
  height: 42px;
  border: 1px solid var(--ui-line-strong);
  border-radius: 10px;
  background: var(--ui-surface);
  color: var(--ui-text);
  padding: 0 12px;
}

.primary-button,
.secondary-button,
.text-button {
  min-height: 42px;
  border-radius: 10px;
  padding: 0 15px;
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
}

.primary-button {
  border: 1px solid var(--ui-accent);
  background: var(--ui-accent);
  color: #fff;
}

.primary-button:hover {
  border-color: var(--ui-accent-hover);
  background: var(--ui-accent-hover);
}

.primary-button.compact {
  min-height: 36px;
}

.secondary-button {
  border: 1px solid var(--ui-line-strong);
  background: var(--ui-surface);
  color: var(--ui-text-secondary);
}

.secondary-button:hover {
  border-color: var(--ui-accent);
  color: var(--ui-accent-ink);
}

.text-button {
  border: 0;
  background: transparent;
  color: var(--ui-text-secondary);
}

button:disabled {
  cursor: not-allowed;
  opacity: .55;
}

.memory-sync-strip {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  margin-top: 24px;
  border: 1px solid var(--ui-line);
  border-radius: 14px;
  background: var(--ui-surface-active);
  padding: 15px 17px;
}

.sync-mark {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 10px;
  background: var(--ui-accent);
  color: #fff;
  font: 850 14px/1 var(--ui-font-display);
}

.sync-copy strong {
  display: block;
  font-size: 13px;
}

.sync-copy p {
  margin: 4px 0 0;
  color: var(--ui-text-muted);
  font-size: 11px;
  line-height: 1.55;
}

.memory-error,
.memory-notice {
  margin-top: 14px;
  border-radius: 10px;
  padding: 11px 14px;
}

.memory-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  border: 1px solid var(--ui-danger, #b54747);
  background: var(--ui-danger-soft, #fff3f3);
}

.memory-error strong {
  color: var(--ui-danger, #b54747);
  font-size: 12px;
}

.memory-error p {
  margin: 3px 0 0;
  color: var(--ui-text-secondary);
  font-size: 11px;
}

.memory-error button {
  border: 0;
  background: transparent;
  color: var(--ui-danger, #b54747);
  font-size: 11px;
  font-weight: 800;
  cursor: pointer;
}

.memory-notice {
  border-left: 3px solid var(--ui-success, #247a5a);
  background: var(--ui-success-soft, #edf9f4);
  color: var(--ui-success, #247a5a);
  font-size: 11px;
  font-weight: 750;
}

.memory-loading {
  display: flex;
  min-height: 260px;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--ui-text-muted);
  font-size: 12px;
}

.memory-loading span {
  width: 18px;
  height: 18px;
  border: 2px solid var(--ui-line-strong);
  border-top-color: var(--ui-accent);
  border-radius: 50%;
  animation: memory-loading-spin .75s linear infinite;
}

.memory-content {
  margin-top: 25px;
}

.memory-content-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 13px;
}

.memory-content-header h2 {
  margin: 0;
  font: 850 19px/1.3 var(--ui-font-display);
}

.memory-content-header p {
  margin: 4px 0 0;
  color: var(--ui-text-muted);
  font-size: 11px;
}

.memory-status-key {
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--ui-text-muted);
  font-size: 10px;
}

.memory-status-key span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.memory-status-key i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.memory-status-key .active i {
  background: var(--ui-success, #247a5a);
}

.memory-status-key .candidate i {
  background: var(--ui-warning, #9a650d);
}

.memory-status-key .disabled i {
  background: var(--ui-text-muted);
}

.memory-card-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: 15px;
}

.memory-group-card {
  position: relative;
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--ui-line);
  border-radius: 14px;
  background: var(--ui-surface);
  padding: 18px;
  box-shadow: var(--ui-shadow);
}

.memory-group-card::before {
  position: absolute;
  top: 20px;
  left: 0;
  width: 3px;
  height: 38px;
  border-radius: 0 3px 3px 0;
  background: var(--ui-accent);
  content: "";
}

.memory-group-header {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: start;
  gap: 11px;
}

.memory-type-mark {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 9px;
  background: var(--ui-surface-active);
  color: var(--ui-accent-ink);
  font: 850 13px/1 var(--ui-font-display);
}

.memory-group-header h3 {
  margin: 0;
  font: 850 15px/1.35 var(--ui-font-display);
}

.memory-group-header p {
  margin: 4px 0 0;
  color: var(--ui-text-muted);
  font-size: 10px;
  line-height: 1.55;
}

.memory-group-header > span {
  border-radius: 999px;
  background: var(--ui-surface-soft);
  color: var(--ui-text-muted);
  padding: 4px 7px;
  font-size: 9px;
  font-weight: 800;
}

.memory-group-empty {
  display: flex;
  min-height: 74px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 16px;
  border-radius: 10px;
  background: var(--ui-surface-soft);
  padding: 13px 14px;
}

.memory-group-empty p {
  margin: 0;
  color: var(--ui-text-muted);
  font-size: 11px;
}

.memory-group-empty button,
.memory-card-add {
  border: 0;
  background: transparent;
  color: var(--ui-accent-ink);
  font-size: 11px;
  font-weight: 800;
  cursor: pointer;
}

.memory-editor {
  display: grid;
  gap: 10px;
  margin-top: 16px;
  border: 1px solid var(--ui-accent);
  border-radius: 11px;
  background: var(--ui-surface-active);
  padding: 13px;
}

.memory-editor label {
  color: var(--ui-accent-ink);
  font-size: 11px;
  font-weight: 850;
}

.memory-editor textarea {
  box-sizing: border-box;
  width: 100%;
  min-height: 82px;
  resize: vertical;
  border: 1px solid var(--ui-line-strong);
  border-radius: 9px;
  outline: 0;
  background: var(--ui-surface);
  color: var(--ui-text);
  padding: 10px 11px;
  font-size: 12px;
  line-height: 1.65;
}

.memory-editor textarea:focus {
  border-color: var(--ui-accent);
  box-shadow: 0 0 0 3px var(--ui-focus);
}

.memory-editor footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 7px;
}

.memory-editor footer small {
  margin-right: auto;
  color: var(--ui-text-muted);
  font-size: 9px;
}

.memory-group-items {
  max-height: 310px;
  overflow-y: auto;
  margin-top: 15px;
  padding-right: 3px;
}

.memory-item {
  padding: 13px 2px;
  border-top: 1px solid var(--ui-line);
}

.memory-item:first-child {
  border-top: 0;
  padding-top: 2px;
}

.memory-item.status-disabled {
  opacity: .72;
}

.memory-item-meta {
  display: flex;
  align-items: center;
  gap: 7px;
}

.memory-item-meta > span {
  border-radius: 999px;
  padding: 3px 7px;
  font-size: 9px;
  font-weight: 850;
}

.memory-item-meta > .active {
  background: var(--ui-success-soft, #edf9f4);
  color: var(--ui-success, #247a5a);
}

.memory-item-meta > .candidate {
  background: var(--ui-warning-soft, #fff8e8);
  color: var(--ui-warning, #9a650d);
}

.memory-item-meta > .disabled {
  background: var(--ui-surface-soft);
  color: var(--ui-text-muted);
}

.memory-item-meta small {
  color: var(--ui-text-muted);
  font-size: 9px;
}

.memory-item > p {
  margin: 9px 0 10px;
  color: var(--ui-text);
  font-size: 12px;
  line-height: 1.65;
}

.memory-card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.memory-card-actions button {
  min-height: 29px;
  border: 1px solid var(--ui-line-strong);
  border-radius: 7px;
  background: var(--ui-surface);
  color: var(--ui-text-secondary);
  padding: 0 9px;
  font-size: 9px;
  font-weight: 800;
  cursor: pointer;
}

.memory-card-actions button:hover {
  border-color: var(--ui-accent);
  color: var(--ui-accent-ink);
}

.memory-card-actions button.primary {
  border-color: var(--ui-accent);
  background: var(--ui-accent);
  color: #fff;
}

.memory-card-actions button.danger {
  margin-left: auto;
  color: var(--ui-danger, #b54747);
}

.memory-card-add {
  margin-top: 10px;
  padding: 6px 2px 0;
}

@keyframes memory-loading-spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 1100px) {
  .memory-page-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .memory-header-actions {
    width: 100%;
  }

  .memory-space-field {
    flex: 1;
  }

  .memory-space-field select {
    width: 100%;
  }

  .memory-card-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .career-memory-page {
    padding: 22px 18px 40px;
  }

  .memory-header-actions,
  .memory-content-header {
    align-items: stretch;
    flex-direction: column;
  }

  .memory-sync-strip {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .memory-sync-strip .secondary-button {
    grid-column: 1 / -1;
  }

  .memory-status-key {
    flex-wrap: wrap;
  }
}

@media (prefers-reduced-motion: reduce) {
  .memory-loading span {
    animation: none;
  }
}
</style>
