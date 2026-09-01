<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

const loading = ref(true)
const errorMessage = ref('')
const noticeMessage = ref('')
const activeView = ref('overview')
const overview = ref({ summary: {}, source_distribution: [], experiments: [] })
const datasets = ref([])
const metrics = ref([])
const selectedExperiment = ref(null)
const selectedExperimentLoading = ref(false)
const experimentRunning = ref(false)
const showDatasetForm = ref(false)
const showExperimentForm = ref(false)
const versionDraft = reactive({ datasetId: '', versionLabel: '' })
const caseDraft = reactive({
  versionId: '',
  caseKey: '',
  sourceKind: 'anonymized_real',
  sourceRefId: '',
  inputJson: '{\n  "query": ""\n}',
  expectationJson: '{\n  "relevant_chunk_ids": []\n}',
  reviewerIds: '',
})
const datasetDraft = reactive({ name: '', targetType: 'interview_rag', description: '' })
const experimentDraft = reactive({
  name: '',
  targetType: 'interview_rag',
  datasetVersionId: '',
  gitCommit: '',
  promptVersion: '',
  modelProvider: '',
  modelId: '',
  retrievalStrategy: '',
  repetitions: 1,
  configJson: '{\n  "lexical_candidate_limit": 24,\n  "semantic_candidate_limit": 24,\n  "final_limit": 6,\n  "reciprocal_rank_fusion_k": 60,\n  "embedding_enabled": false,\n  "embedding_model_id": null\n}',
})

const summary = computed(() => overview.value?.summary || {})
const experiments = computed(() => overview.value?.experiments || [])
const sourceDistribution = computed(() => overview.value?.source_distribution || [])
const realCaseRatio = computed(() => {
  const total = Number(summary.value.case_count || 0)
  return total ? Math.round((Number(summary.value.real_case_count || 0) / total) * 100) : 0
})
const maxSourceCount = computed(() => Math.max(1, ...sourceDistribution.value.map((item) => Number(item.case_count || 0))))
const frozenVersions = computed(() => datasets.value.flatMap((dataset) => (
  (dataset.versions || [])
    .filter((version) => version.status === 'frozen')
    .map((version) => ({
      ...version,
      datasetName: dataset.name,
      targetType: dataset.target_type,
    }))
)))

onMounted(loadAll)

async function loadAll() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [overviewPayload, datasetPayload, metricPayload] = await Promise.all([
      requestJson('/api/evaluations/overview'),
      requestJson('/api/evaluations/datasets'),
      requestJson('/api/evaluations/metric-definitions'),
    ])
    overview.value = overviewPayload
    datasets.value = datasetPayload.items || []
    metrics.value = metricPayload.items || []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法读取评测数据'
  } finally {
    loading.value = false
  }
}

async function createDataset() {
  await runAction(async () => {
    await requestJson('/api/evaluations/datasets', {
      method: 'POST',
      body: JSON.stringify({
        name: datasetDraft.name,
        target_type: datasetDraft.targetType,
        description: datasetDraft.description,
      }),
    })
    Object.assign(datasetDraft, { name: '', targetType: 'interview_rag', description: '' })
    showDatasetForm.value = false
    noticeMessage.value = '数据集已创建，请继续建立草稿版本并录入真实案例。'
    await loadAll()
  })
}

function startVersion(dataset) {
  versionDraft.datasetId = dataset.id
  versionDraft.versionLabel = ''
}

async function createVersion() {
  await runAction(async () => {
    await requestJson(`/api/evaluations/datasets/${versionDraft.datasetId}/versions`, {
      method: 'POST',
      body: JSON.stringify({ version_label: versionDraft.versionLabel }),
    })
    noticeMessage.value = '数据版本已创建。冻结前可以持续录入并复核案例。'
    Object.assign(versionDraft, { datasetId: '', versionLabel: '' })
    await loadAll()
  })
}

function startCase(version) {
  caseDraft.versionId = version.id
  caseDraft.caseKey = ''
  caseDraft.sourceKind = 'anonymized_real'
  caseDraft.sourceRefId = ''
  caseDraft.reviewerIds = ''
}

async function createCase() {
  await runAction(async () => {
    const input = parseJsonObject(caseDraft.inputJson, '案例输入')
    const expectation = parseJsonObject(caseDraft.expectationJson, '案例期望')
    const reviewerIds = caseDraft.reviewerIds.split(',').map((item) => item.trim()).filter(Boolean)
    await requestJson(`/api/evaluations/dataset-versions/${caseDraft.versionId}/cases`, {
      method: 'POST',
      body: JSON.stringify({
        case_key: caseDraft.caseKey,
        source_kind: caseDraft.sourceKind,
        source_ref_id: caseDraft.sourceRefId || null,
        input,
        expectation,
        metadata: {
          label_status: 'approved',
          anonymization_status: caseDraft.sourceKind === 'synthetic' ? 'not_applicable' : 'verified',
          reviewer_ids: reviewerIds,
        },
      }),
    })
    noticeMessage.value = '案例已写入草稿版本，并生成不可变内容哈希。'
    caseDraft.versionId = ''
    await loadAll()
  })
}

async function freezeVersion(version) {
  if (!window.confirm(`冻结 ${version.version_label} 后将不能继续添加案例，确认继续？`)) return
  await runAction(async () => {
    await requestJson(`/api/evaluations/dataset-versions/${version.id}/freeze`, { method: 'POST' })
    noticeMessage.value = '数据版本已冻结，可以基于这个固定版本创建实验。'
    await loadAll()
  })
}

async function openExperimentForm() {
  showExperimentForm.value = true
  if (frozenVersions.value.length) {
    const version = frozenVersions.value[0]
    experimentDraft.datasetVersionId = version.id
    experimentDraft.targetType = version.targetType
  }
  if (experimentDraft.targetType !== 'interview_rag') return
  await runAction(async () => {
    const payload = await requestJson('/api/evaluations/runtime-config/interview-rag')
    experimentDraft.configJson = JSON.stringify(payload.config || {}, null, 2)
    experimentDraft.retrievalStrategy = payload.config?.embedding_enabled
      ? 'hybrid_rrf_with_lexical_fallback'
      : 'lexical'
  })
}

function syncExperimentTarget() {
  const version = frozenVersions.value.find((item) => item.id === experimentDraft.datasetVersionId)
  if (version) experimentDraft.targetType = version.targetType
}

async function createExperiment() {
  await runAction(async () => {
    const config = parseJsonObject(experimentDraft.configJson, '实验配置')
    await requestJson('/api/evaluations/experiments', {
      method: 'POST',
      body: JSON.stringify({
        name: experimentDraft.name,
        target_type: experimentDraft.targetType,
        dataset_version_id: experimentDraft.datasetVersionId,
        git_commit: experimentDraft.gitCommit || null,
        prompt_version: experimentDraft.promptVersion || null,
        model_provider: experimentDraft.modelProvider || null,
        model_id: experimentDraft.modelId || null,
        retrieval_strategy: experimentDraft.retrievalStrategy || null,
        repetitions: Number(experimentDraft.repetitions),
        config,
      }),
    })
    noticeMessage.value = '实验身份已锁定。运行 Adapter 可以开始提交逐案例 Observation。'
    showExperimentForm.value = false
    experimentDraft.name = ''
    await loadAll()
  })
}

async function executeInterviewRagExperiment() {
  if (!selectedExperiment.value || experimentRunning.value) return
  if (!window.confirm('将按冻结案例调用当前真实 RAG 检索链路，并写入不可变 Run。确认执行？')) return
  experimentRunning.value = true
  await runAction(async () => {
    const result = await requestJson(`/api/evaluations/experiments/${selectedExperiment.value.id}/execute-interview-rag`, {
      method: 'POST',
    })
    noticeMessage.value = `实验完成：新增 ${result.executed_run_count} 次运行，跳过 ${result.skipped_run_count} 次，失败 ${result.failed_run_count} 次。`
    await loadAll()
    await openExperiment({ id: result.experiment.id })
  })
  experimentRunning.value = false
}

async function openExperiment(experiment) {
  activeView.value = 'experiments'
  selectedExperimentLoading.value = true
  errorMessage.value = ''
  try {
    const payload = await requestJson(`/api/evaluations/experiments/${experiment.id}`)
    selectedExperiment.value = payload.item
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法读取实验详情'
  } finally {
    selectedExperimentLoading.value = false
  }
}

async function runAction(handler) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    await handler()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: 'include',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    let detail = '请求失败'
    try { detail = (await response.json()).detail || detail } catch { /* 使用默认提示 */ }
    throw new Error(detail)
  }
  return response.json()
}

function parseJsonObject(text, label) {
  let value
  try { value = JSON.parse(text) } catch { throw new Error(`${label}不是合法 JSON`) }
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(`${label}必须是 JSON 对象`)
  return value
}

function sourceLabel(value) {
  return {
    anonymized_real: '真实脱敏',
    failure_replay: '失败回放',
    curated_edge: '人工边界',
    synthetic: '合成补充',
  }[value] || value
}

function targetLabel(value) {
  return { interview_rag: '面经 RAG', career_agent: '求职 Agent', resume_optimizer: '简历优化' }[value] || value
}

function statusLabel(value) {
  return { draft: '草稿', frozen: '已冻结', running: '运行中', completed: '已完成', failed: '失败' }[value] || value
}

function formatDate(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function formatMetricValue(metric) {
  const value = metric.aggregation === 'p95' ? metric.p95_value : metric.mean_value
  if (value === null || value === undefined) return '—'
  if (metric.unit === 'ratio') return `${(Number(value) * 100).toFixed(1)}%`
  if (metric.unit === 'ms') return `${Math.round(Number(value)).toLocaleString()} ms`
  return Number(value).toFixed(3)
}

function metricPass(metric) {
  const value = Number(metric.aggregation === 'p95' ? metric.p95_value : metric.mean_value)
  return metric.direction === 'higher_is_better' ? value >= Number(metric.threshold) : value <= Number(metric.threshold)
}
</script>

<template>
  <section class="evaluation-page">
    <header class="evaluation-heading">
      <div>
        <p class="eyebrow">EVIDENCE-BOUND EVALUATION</p>
        <h2>评测证据中心</h2>
        <p>每个结论都绑定真实样本、冻结版本、实际运行和可复算指标。</p>
      </div>
      <div class="evaluation-actions">
        <button class="secondary-button" type="button" @click="loadAll">刷新数据</button>
        <button type="button" @click="openExperimentForm">创建实验</button>
      </div>
    </header>

    <nav class="evaluation-tabs" aria-label="评测中心视图">
      <button v-for="item in [
        ['overview', '总览'], ['datasets', '真实数据集'], ['experiments', '实验记录'], ['metrics', '指标字典']
      ]" :key="item[0]" type="button" :class="{ active: activeView === item[0] }" @click="activeView = item[0]">
        {{ item[1] }}
      </button>
    </nav>

    <p v-if="noticeMessage" class="evaluation-notice">{{ noticeMessage }}</p>
    <p v-if="errorMessage" class="evaluation-error">{{ errorMessage }}</p>
    <section v-if="loading" class="evaluation-state">正在核对数据版本、实验和指标定义…</section>

    <template v-else-if="activeView === 'overview'">
      <section class="evaluation-chain" aria-label="评测证据链">
        <article>
          <span>真实案例</span>
          <strong>{{ summary.real_case_count || 0 }} / {{ summary.case_count || 0 }}</strong>
          <small>真实数据占比 {{ realCaseRatio }}%</small>
        </article>
        <i aria-hidden="true">→</i>
        <article>
          <span>冻结版本</span>
          <strong>{{ summary.frozen_version_count || 0 }}</strong>
          <small>内容哈希锁定输入与金标</small>
        </article>
        <i aria-hidden="true">→</i>
        <article>
          <span>已完成实验</span>
          <strong>{{ summary.completed_experiment_count || 0 }}</strong>
          <small>共登记 {{ summary.experiment_count || 0 }} 次实验</small>
        </article>
        <i aria-hidden="true">→</i>
        <article>
          <span>发布依据</span>
          <strong>{{ experiments.some((item) => item.status === 'completed' && Number(item.critical_failure_count) === 0) ? '可审查' : '待建立' }}</strong>
          <small>红线优先于所有平均分</small>
        </article>
      </section>

      <section class="evaluation-overview-grid">
        <article class="evaluation-panel">
          <header><div><p class="eyebrow">DATA LINEAGE</p><h3>数据来源构成</h3></div><button class="text-button" type="button" @click="activeView = 'datasets'">管理数据</button></header>
          <div v-if="sourceDistribution.length" class="evaluation-source-bars">
            <div v-for="item in sourceDistribution" :key="item.source_kind">
              <span>{{ sourceLabel(item.source_kind) }}</span>
              <div><i :style="{ width: `${Math.max(4, Number(item.case_count) / maxSourceCount * 100)}%` }"></i></div>
              <strong>{{ item.case_count }}</strong>
            </div>
          </div>
          <p v-else class="evaluation-empty">尚无案例。先建立数据集和草稿版本，再录入经过脱敏与复核的真实数据。</p>
        </article>

        <article class="evaluation-panel">
          <header><div><p class="eyebrow">RELEASE LEDGER</p><h3>最近实验</h3></div><button class="text-button" type="button" @click="activeView = 'experiments'">查看全部</button></header>
          <div v-if="experiments.length" class="evaluation-experiment-list compact">
            <button v-for="item in experiments.slice(0, 5)" :key="item.id" type="button" @click="openExperiment(item)">
              <span><strong>{{ item.name }}</strong><small>{{ targetLabel(item.target_type) }} · {{ item.dataset_version }}</small></span>
              <span class="evaluation-run-state" :class="item.status">{{ statusLabel(item.status) }}</span>
              <span :class="Number(item.critical_failure_count) ? 'failed' : 'passed'">红线 {{ item.critical_failure_count || 0 }}</span>
            </button>
          </div>
          <p v-else class="evaluation-empty">还没有实验。数据版本冻结后，锁定代码、模型、Prompt 和检索策略创建 baseline。</p>
        </article>
      </section>
    </template>

    <template v-else-if="activeView === 'datasets'">
      <section class="evaluation-section-heading">
        <div><h3>真实数据集</h3><p>合成案例可以补充覆盖，但不会被计入真实样本数量。</p></div>
        <button type="button" @click="showDatasetForm = !showDatasetForm">{{ showDatasetForm ? '取消' : '新建数据集' }}</button>
      </section>

      <form v-if="showDatasetForm" class="evaluation-form" @submit.prevent="createDataset">
        <label>数据集名称<input v-model="datasetDraft.name" required maxlength="120" placeholder="例如：面经检索真实集" /></label>
        <label>评测目标<select v-model="datasetDraft.targetType"><option value="interview_rag">面经 RAG</option><option value="career_agent">求职 Agent</option><option value="resume_optimizer">简历优化</option></select></label>
        <label class="wide">说明<textarea v-model="datasetDraft.description" rows="2" placeholder="记录数据范围与采样原则"></textarea></label>
        <div class="wide"><button type="submit">创建数据集</button></div>
      </form>

      <section v-if="datasets.length" class="evaluation-dataset-list">
        <article v-for="dataset in datasets" :key="dataset.id">
          <header>
            <div><span>{{ targetLabel(dataset.target_type) }}</span><h3>{{ dataset.name }}</h3><p>{{ dataset.description || '未填写数据范围说明' }}</p></div>
            <button class="secondary-button" type="button" @click="startVersion(dataset)">新增版本</button>
          </header>
          <form v-if="versionDraft.datasetId === dataset.id" class="evaluation-inline-form" @submit.prevent="createVersion">
            <input v-model="versionDraft.versionLabel" required placeholder="版本标签，如 rag-gold-v1.0" />
            <button type="submit">建立草稿版本</button>
            <button class="text-button" type="button" @click="versionDraft.datasetId = ''">取消</button>
          </form>
          <div class="evaluation-version-list">
            <div v-for="version in dataset.versions" :key="version.id">
              <span class="evaluation-run-state" :class="version.status">{{ statusLabel(version.status) }}</span>
              <strong>{{ version.version_label }}</strong>
              <small>{{ version.case_count }} 个案例 · {{ version.content_hash ? version.content_hash.slice(0, 10) : '尚未生成版本哈希' }}</small>
              <button v-if="version.status === 'draft'" class="text-button" type="button" @click="startCase(version)">录入案例</button>
              <button v-if="version.status === 'draft'" class="text-button danger" type="button" @click="freezeVersion(version)">冻结</button>
            </div>
          </div>
        </article>
      </section>
      <p v-else class="evaluation-state">尚未建立评测数据集。</p>

      <form v-if="caseDraft.versionId" class="evaluation-case-form" @submit.prevent="createCase">
        <header><div><p class="eyebrow">DATA ADMISSION</p><h3>录入已复核案例</h3></div><button class="text-button" type="button" @click="caseDraft.versionId = ''">关闭</button></header>
        <div class="evaluation-form">
          <label>案例编号<input v-model="caseDraft.caseKey" required placeholder="例如 rag-real-0001" /></label>
          <label>数据来源<select v-model="caseDraft.sourceKind"><option value="anonymized_real">真实脱敏</option><option value="failure_replay">历史失败回放</option><option value="curated_edge">人工边界</option><option value="synthetic">合成补充</option></select></label>
          <label>来源记录 UUID<input v-model="caseDraft.sourceRefId" :required="['anonymized_real', 'failure_replay'].includes(caseDraft.sourceKind)" placeholder="RAG Feedback / Agent Turn / 简历记录 UUID" /></label>
          <label>复核人<input v-model="caseDraft.reviewerIds" required placeholder="至少两人，用逗号分隔" /></label>
          <label class="wide">输入 JSON<textarea v-model="caseDraft.inputJson" rows="7" spellcheck="false"></textarea></label>
          <label class="wide">期望 JSON<textarea v-model="caseDraft.expectationJson" rows="7" spellcheck="false"></textarea></label>
          <div class="wide evaluation-form-footer"><span>真实数据会写入“已脱敏、已审核”准入元数据；冻结时再次校验双人复核。</span><button type="submit">写入草稿版本</button></div>
        </div>
      </form>
    </template>

    <template v-else-if="activeView === 'experiments'">
      <section class="evaluation-section-heading"><div><h3>实验记录</h3><p>同一冻结数据版本上的结果才能形成可信的版本比较。</p></div><button type="button" @click="openExperimentForm">创建实验</button></section>
      <form v-if="showExperimentForm" class="evaluation-form" @submit.prevent="createExperiment">
        <label>实验名称<input v-model="experimentDraft.name" required placeholder="例如 hybrid-rrf-baseline-v1" /></label>
        <label>冻结数据版本<select v-model="experimentDraft.datasetVersionId" required @change="syncExperimentTarget"><option value="" disabled>选择版本</option><option v-for="version in frozenVersions" :key="version.id" :value="version.id">{{ version.datasetName }} / {{ version.version_label }}</option></select></label>
        <label>Git Commit<input v-model="experimentDraft.gitCommit" placeholder="可选，但正式实验建议填写" /></label>
        <label>Prompt 版本<input v-model="experimentDraft.promptVersion" placeholder="例如 career-response-v3" /></label>
        <label>模型 Provider<input v-model="experimentDraft.modelProvider" placeholder="例如 deepseek" /></label>
        <label>模型 ID<input v-model="experimentDraft.modelId" placeholder="例如 deepseek-chat" /></label>
        <label>检索策略<input v-model="experimentDraft.retrievalStrategy" placeholder="例如 hybrid_rrf" /></label>
        <label>重复次数<input v-model.number="experimentDraft.repetitions" type="number" min="1" max="20" /></label>
        <label class="wide">运行配置 JSON<textarea v-model="experimentDraft.configJson" rows="8" spellcheck="false" /></label>
        <div class="wide evaluation-form-footer"><span>创建后实验身份哈希不可变；实际 Adapter 按案例提交 Observation。</span><button type="submit">锁定实验配置</button></div>
      </form>

      <section class="evaluation-experiment-layout">
        <div class="evaluation-experiment-list">
          <button v-for="item in experiments" :key="item.id" type="button" :class="{ active: selectedExperiment?.id === item.id }" @click="openExperiment(item)">
            <span><strong>{{ item.name }}</strong><small>{{ targetLabel(item.target_type) }} · {{ item.dataset_version }}</small></span>
            <span>{{ item.run_count || 0 }} runs</span>
            <span class="evaluation-run-state" :class="item.status">{{ statusLabel(item.status) }}</span>
            <span :class="Number(item.critical_failure_count) ? 'failed' : 'passed'">红线 {{ item.critical_failure_count || 0 }}</span>
          </button>
          <p v-if="!experiments.length" class="evaluation-empty">暂无实验记录。</p>
        </div>

        <article class="evaluation-experiment-detail">
          <p v-if="selectedExperimentLoading" class="evaluation-empty">正在读取实验事实…</p>
          <template v-else-if="selectedExperiment">
            <header><div><span>{{ statusLabel(selectedExperiment.status) }}</span><h3>{{ selectedExperiment.name }}</h3><p>{{ selectedExperiment.model_provider || '未记录 Provider' }} / {{ selectedExperiment.model_id || '未记录模型' }} · {{ selectedExperiment.retrieval_strategy || '无检索策略' }}</p></div><div class="evaluation-experiment-actions"><code>{{ selectedExperiment.config_hash.slice(0, 12) }}</code><button v-if="selectedExperiment.target_type === 'interview_rag' && selectedExperiment.status !== 'completed'" type="button" :disabled="experimentRunning" @click="executeInterviewRagExperiment">{{ experimentRunning ? '真实检索执行中…' : '执行真实 RAG 实验' }}</button></div></header>
            <div v-if="selectedExperiment.metrics.length" class="evaluation-metric-results">
              <div v-for="metric in selectedExperiment.metrics" :key="metric.metric_key" :class="{ critical: metric.critical }">
                <span>{{ metric.display_name }}<small>{{ metric.aggregation }} · n={{ metric.sample_count }}</small></span>
                <strong :class="metricPass(metric) ? 'passed' : 'failed'">{{ formatMetricValue(metric) }}</strong>
                <small>阈值 {{ metric.direction === 'higher_is_better' ? '≥' : '≤' }} {{ metric.unit === 'ratio' ? `${metric.threshold * 100}%` : metric.threshold }}</small>
              </div>
            </div>
            <p v-else class="evaluation-empty">实验配置已锁定，但尚未收到运行 Observation。</p>
            <div v-if="selectedExperiment.runs.length" class="evaluation-run-table"><div class="heading"><span>案例</span><span>来源</span><span>耗时</span><span>失败指标</span></div><div v-for="run in selectedExperiment.runs" :key="run.id"><span>{{ run.case_key }} / #{{ run.repetition }}</span><span>{{ sourceLabel(run.source_kind) }}</span><span>{{ run.latency_ms }} ms</span><span :class="Number(run.failed_metric_count) ? 'failed' : 'passed'">{{ run.failed_metric_count }}</span></div></div>
          </template>
          <p v-else class="evaluation-empty">选择一个实验，查看指标聚合、运行数量和失败案例。</p>
        </article>
      </section>
    </template>

    <template v-else-if="activeView === 'metrics'">
      <section class="evaluation-section-heading"><div><h3>指标字典</h3><p>公式、方向、阈值和版本共同决定一个指标的含义。</p></div></section>
      <section class="evaluation-metric-dictionary">
        <article v-for="metric in metrics" :key="`${metric.metric_key}-${metric.version}`" :class="{ critical: metric.critical }">
          <header><span>{{ targetLabel(metric.target_type) }}</span><strong>{{ metric.display_name }}</strong><code>{{ metric.metric_key }}@{{ metric.version }}</code></header>
          <p>{{ metric.description }}</p>
          <dl><div><dt>公式</dt><dd>{{ metric.formula }}</dd></div><div><dt>聚合</dt><dd>{{ metric.aggregation }}</dd></div><div><dt>门槛</dt><dd>{{ metric.direction === 'higher_is_better' ? '≥' : '≤' }} {{ metric.unit === 'ratio' ? `${metric.threshold * 100}%` : `${metric.threshold} ${metric.unit}` }}</dd></div><div><dt>评测器</dt><dd>{{ metric.evaluator_kind }}</dd></div></dl>
        </article>
      </section>
    </template>
  </section>
</template>
