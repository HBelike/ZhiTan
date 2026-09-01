<script setup>
import { computed, ref, watch } from 'vue'
import CareerJobCanvas from './CareerJobCanvas.vue'
import { buildAssessmentCards, itemsForDimension } from '../career-assessment-view.js'

const props = defineProps({
  context: { type: Object, default: null },
  selectedConversation: { type: Object, default: null },
  wide: { type: Boolean, default: false }
})

const emit = defineEmits(['adjust', 'setup', 'retry-assessment'])
const selectedMetric = ref('')
const showMetricDialog = ref(false)

const metrics = computed(() => buildAssessmentCards(props.context?.assessment))
const activeMetric = computed(() => metrics.value.find((item) => item.key === selectedMetric.value) ?? metrics.value[0])
const visibleRequirements = computed(() => itemsForDimension(props.context?.assessment, selectedMetric.value))
watch(() => props.context?.binding_id, () => {
  selectedMetric.value = metrics.value[0]?.key ?? ''
  showMetricDialog.value = false
})
watch(metrics, (items) => {
  if (!items.some((item) => item.key === selectedMetric.value)) selectedMetric.value = items[0]?.key ?? ''
}, { immediate: true })

function openMetricDialog(metricKey) {
  selectedMetric.value = metricKey
  showMetricDialog.value = true
}

function formatScore(metric) {
  return metric?.status === 'ready' && metric.score !== null ? `${metric.score}%` : '待分析'
}

function matchLabel(item) {
  if (item.verdict === 'supported') return '已有证据'
  if (item.verdict === 'partial') return '部分证据'
  if (item.verdict === 'unsupported') return '待补证'
  if (item.verdict === 'needs_confirmation') return '需要确认'
  const factor = activeDimensionMatch(item)?.factor ?? item.match_factor
  if (factor === 1) return '已有证据'
  if (factor === 0.5) return '部分证据'
  if (factor === 0) return '待补证'
  return '未纳入计算'
}

function activeDimensionMatch(item) {
  const dimension = {
    skill_coverage: 'skill',
    experience_coverage: 'experience',
    project_relevance: 'project'
  }[selectedMetric.value]
  return dimension ? item.dimension_matches?.[dimension] : null
}

function activeMatchFactor(item) {
  return item.factor ?? activeDimensionMatch(item)?.factor ?? item.match_factor
}

function requirementLabel(item) {
  if (item.requirement_type === 'required') return '必须'
  if (item.requirement_type === 'preferred') return '加分'
  if (item.requirement_type === 'context_only') return '信息'
  return item.priority === 'must' ? '必须' : '加分'
}

function requirementText(item) {
  return item.jd_evidence?.join('；') || item.text
}

function evidenceLines(item) {
  if (Array.isArray(item.cv_evidence)) return item.cv_evidence
  const evidence = activeDimensionMatch(item)?.evidence ?? item.evidence
  return evidence ? [evidence] : []
}
</script>

<template>
  <aside class="context-rail" aria-label="目标岗位与岗位适配指标">
    <template v-if="context">
      <header class="rail-header">
        <div>
          <p>{{ context.target_role ? '职位信息' : '已添加简历' }}</p>
          <template v-if="!context.target_role">
            <h2>{{ context.candidate_profile?.display_name }}</h2>
            <span>后续回答会使用这份个人经历资料</span>
          </template>
        </div>
        <button type="button" @click="emit('adjust')">管理资料</button>
      </header>

      <div v-if="context.candidate_profile && !context.target_role" class="context-binding">
        <span>基准简历</span>
        <strong>{{ context.candidate_profile.display_name }}</strong>
        <small>{{ context.candidate_profile.source_filename }} · 简历版本 {{ context.candidate_profile.version }}</small>
      </div>
      <div v-else-if="!context.candidate_profile && !context.target_role" class="context-binding context-missing">
        <span>基准简历</span>
        <strong>尚未添加</strong>
        <small>不影响普通对话；添加后可结合个人经历分析。</small>
      </div>

      <section v-if="context.target_role" class="jd-canvas-section">
        <CareerJobCanvas
          :target-role="context.target_role"
          :metrics="metrics"
          :assessment="context.assessment"
          :candidate-profile="context.candidate_profile"
          :wide="wide"
          @select-metric="openMetricDialog"
          @retry-assessment="emit('retry-assessment')"
        />
      </section>

      <section v-if="!context.assessment && (!context.candidate_profile || !context.target_role)" class="context-gap">
        <h3>匹配画像暂未开启</h3>
        <p>{{ context.candidate_profile ? '再添加目标岗位后，系统会结合简历证据生成岗位适配指标。' : '再添加简历后，系统会结合岗位要求生成个性化匹配画像。' }}</p>
        <button type="button" @click="emit('adjust')">补充另一项资料</button>
      </section>
    </template>

    <section v-else class="rail-empty">
      <span>职</span>
      <h2>{{ selectedConversation ? '这个旧会话还没有岗位上下文' : '先确定目标，再开始对话' }}</h2>
      <p>确认基准简历与唯一目标岗位后，这里会持续显示职位信息、岗位适配指标和逐项证据。</p>
      <button type="button" @click="emit('setup')">{{ selectedConversation ? '基于新上下文开启对话' : '建立求职上下文' }}</button>
    </section>
  </aside>

  <Teleport to="body">
    <div v-if="showMetricDialog" class="metric-dialog-backdrop" @mousedown.self="showMetricDialog = false" @keydown.esc="showMetricDialog = false">
      <section class="metric-dialog" role="dialog" aria-modal="true" :aria-label="`${activeMetric?.short || '岗位指标'}评分依据`">
        <header class="metric-dialog-header">
          <div>
            <span>岗位适配指标</span>
            <h2>{{ activeMetric?.short }}</h2>
            <p>{{ activeMetric?.description }}</p>
          </div>
          <div class="metric-dialog-score">
            <strong>{{ formatScore(activeMetric) }}</strong>
            <small v-if="activeMetric?.status === 'ready'">{{ activeMetric.item_count ? `${activeMetric.item_count} 项分析依据` : '评分依据见下方' }}</small>
          </div>
          <button type="button" aria-label="关闭评分详情" @click="showMetricDialog = false">×</button>
        </header>
        <main class="metric-dialog-body">
          <div v-if="visibleRequirements.length" class="evidence-list">
            <article v-for="item in visibleRequirements" :key="item.id" :class="`match-${activeMatchFactor(item) ?? 'na'}`">
              <i></i>
              <div>
                <span>{{ requirementLabel(item) }} · 权重 {{ item.weight }}</span>
                <strong>{{ requirementText(item) }}</strong>
                <p v-for="(evidence,index) in evidenceLines(item)" :key="`${item.id}-evidence-${index}`">“{{ evidence }}”</p>
                <small>{{ matchLabel(item) }}</small>
              </div>
            </article>
          </div>
          <div v-else class="ledger-empty">当前职位信息中没有可按确定规则计算的{{ activeMetric?.short }}要求。</div>
        </main>
        <footer>{{ context.assessment.disclaimer }}</footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.context-rail{display:flex;height:100%;min-width:0;min-height:0;box-sizing:border-box;flex-direction:column;overflow-x:hidden;overflow-y:scroll;border:1px solid var(--ui-line);border-radius:12px;background:var(--ui-surface);color:var(--ui-text);scrollbar-color:var(--ui-line-strong) var(--ui-surface-soft);scrollbar-gutter:stable;scrollbar-width:auto}
.context-rail::-webkit-scrollbar{width:10px}.context-rail::-webkit-scrollbar-track{border-radius:0 12px 12px 0;background:var(--ui-surface-soft)}.context-rail::-webkit-scrollbar-thumb{min-height:52px;border:2px solid var(--ui-surface-soft);border-radius:999px;background:var(--ui-line-strong)}.context-rail::-webkit-scrollbar-thumb:hover{background:var(--ui-text-muted)}
.rail-header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;border-bottom:1px solid var(--ui-line);padding:17px 16px 15px}.rail-header p{margin:0;color:var(--ui-accent-ink);font-size:11px;font-weight:800}.rail-header h2{margin:5px 0 4px;font-family:var(--ui-font-display);font-size:19px;line-height:1.35;letter-spacing:-.025em}.rail-header span{color:var(--ui-text-muted);font-size:11px}.rail-header button{flex:none;border:1px solid var(--ui-line-strong);border-radius:8px;background:var(--ui-surface);color:var(--ui-accent-ink);padding:7px 9px;font-size:11px;font-weight:800;cursor:pointer}.rail-header button:hover{background:var(--ui-surface-soft)}
.context-binding{display:grid;gap:3px;margin:12px 14px 0;border-radius:9px;background:var(--ui-surface-soft);padding:10px 11px}.context-binding>span{color:var(--ui-text-muted);font-size:10px;font-weight:800}.context-binding strong{font-size:12px}.context-binding small{overflow:hidden;color:var(--ui-text-muted);font-size:10px;text-overflow:ellipsis;white-space:nowrap}
.context-missing{border:1px dashed var(--ui-line-strong);background:var(--ui-surface)}
.metric-section,.jd-canvas-section{padding:17px 14px 0}.metric-section>header,.jd-canvas-section>header{display:flex;align-items:center;justify-content:space-between;gap:10px}.metric-section h3,.jd-canvas-section h3{margin:0;font-family:var(--ui-font-display);font-size:14px}.metric-section>header>span,.jd-canvas-section>header>span{color:var(--ui-text-muted);font-size:10px;font-weight:700}.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:10px}.metric-grid button{position:relative;display:grid;gap:3px;border:1px solid var(--ui-line);border-radius:9px;background:var(--ui-surface);padding:9px 10px;text-align:left;cursor:pointer}.metric-grid button:hover,.metric-grid button:focus-visible{border-color:var(--ui-accent);background:var(--ui-surface-soft)}.metric-grid button>span:first-child{color:var(--ui-text-secondary);font-size:10px;font-weight:800}.metric-grid button>strong{color:var(--ui-text);font:820 17px/1.2 var(--ui-font-display)}.metric-grid button>small{color:var(--ui-accent-ink);font-size:9px;font-weight:800}.metric-section>p{margin:9px 1px 0;color:var(--ui-text-muted);font-size:10px;line-height:1.55}.metric-tooltip{position:fixed;z-index:1500;display:grid;width:212px;box-sizing:border-box;gap:5px;transform:translateY(-50%);border:1px solid var(--ui-line);border-radius:10px;background:var(--ui-text);box-shadow:0 12px 28px rgba(20,35,55,.2);color:var(--ui-surface);padding:10px;pointer-events:none}.metric-tooltip:after{position:absolute;top:50%;right:-5px;width:9px;height:9px;transform:translateY(-50%) rotate(45deg);background:var(--ui-text);content:""}.metric-tooltip strong{color:inherit;font-size:11px}.metric-tooltip span{color:inherit;font-size:10px;font-weight:500;line-height:1.5}.metric-tooltip small{color:inherit;font-size:9px;opacity:.72}
.jd-canvas-section{margin:0;border:0;padding:14px}.jd-canvas-section>header{margin-bottom:10px}
.metric-dialog-backdrop{position:fixed;z-index:1400;inset:0;display:grid;place-items:center;box-sizing:border-box;padding:28px;background:rgba(15,32,55,.46);backdrop-filter:blur(5px)}.metric-dialog{display:flex;width:min(760px,100%);max-height:min(780px,calc(100vh - 56px));flex-direction:column;overflow:hidden;border:1px solid var(--ui-line);border-radius:20px;background:var(--ui-surface);box-shadow:0 28px 80px rgba(15,32,55,.28);color:var(--ui-text)}.metric-dialog-header{display:grid;grid-template-columns:minmax(0,1fr) auto 34px;align-items:start;gap:18px;border-bottom:1px solid var(--ui-line);padding:22px 24px}.metric-dialog-header>div:first-child>span{color:var(--ui-accent-ink);font-size:10px;font-weight:900}.metric-dialog-header h2{margin:5px 0 4px;font:850 21px/1.3 var(--ui-font-display)}.metric-dialog-header p{margin:0;color:var(--ui-text-muted);font-size:12px;line-height:1.55}.metric-dialog-score{display:grid;justify-items:end}.metric-dialog-score strong{font:900 28px/1.1 var(--ui-font-display)}.metric-dialog-score small{margin-top:4px;color:var(--ui-text-muted);font-size:10px}.metric-dialog-header>button{display:grid;width:34px;height:34px;place-items:center;border:1px solid var(--ui-line);border-radius:9px;background:var(--ui-surface-soft);color:var(--ui-text-secondary);font-size:22px;cursor:pointer}.metric-dialog-body{overflow:auto;padding:10px 24px 18px}.metric-dialog .evidence-list{position:relative;display:grid;margin-top:2px;padding-left:5px}.metric-dialog .evidence-list:before{position:absolute;top:16px;bottom:16px;left:10px;width:1px;background:var(--ui-line);content:""}.metric-dialog .evidence-list article{position:relative;display:grid;grid-template-columns:13px minmax(0,1fr);gap:10px;padding:12px 0;border-bottom:1px solid var(--ui-line)}.metric-dialog .evidence-list article:last-child{border-bottom:0}.metric-dialog .evidence-list article>i{z-index:1;width:8px;height:8px;margin-top:4px;border:2px solid var(--ui-surface);border-radius:50%;background:var(--ui-text-muted);box-shadow:0 0 0 1px var(--ui-text-muted)}.metric-dialog .evidence-list article.match-1>i{background:var(--ui-success,#247a5a);box-shadow:0 0 0 1px var(--ui-success,#247a5a)}.metric-dialog .evidence-list article.match-0\.5>i{background:var(--ui-warning,#9a650d);box-shadow:0 0 0 1px var(--ui-warning,#9a650d)}.metric-dialog .evidence-list article.match-0>i{background:var(--ui-danger,#b54747);box-shadow:0 0 0 1px var(--ui-danger,#b54747)}.metric-dialog .evidence-list article>div{display:grid;gap:4px;min-width:0}.metric-dialog .evidence-list span{color:var(--ui-text-muted);font-size:10px;font-weight:800}.metric-dialog .evidence-list strong{color:var(--ui-text);font-size:12px;line-height:1.55}.metric-dialog .evidence-list p{margin:1px 0 0;border-left:2px solid var(--ui-line-strong);padding-left:8px;color:var(--ui-text-secondary);font-size:11px;line-height:1.6}.metric-dialog .evidence-list small{width:max-content;border-radius:6px;background:var(--ui-surface-soft);color:var(--ui-text-secondary);padding:3px 6px;font-size:9px}.match-1 small{background:var(--ui-success-soft,#edf9f4)!important;color:var(--ui-success,#247a5a)!important}.match-0\.5 small{background:var(--ui-warning-soft,#fff8e8)!important;color:var(--ui-warning,#9a650d)!important}.match-0 small{background:var(--ui-danger-soft,#fff3f3)!important;color:var(--ui-danger,#b54747)!important}.ledger-empty{margin-top:10px;border:1px dashed var(--ui-line-strong);border-radius:9px;padding:15px;color:var(--ui-text-muted);font-size:11px;line-height:1.55}.metric-dialog>footer{border-top:1px solid var(--ui-line);background:var(--ui-surface-soft);color:var(--ui-text-muted);padding:11px 24px;font-size:10px;line-height:1.5}
.context-gap{display:grid;gap:8px;margin:16px 14px 0;border:1px dashed var(--ui-line-strong);border-radius:10px;background:var(--ui-surface-soft);padding:14px}.context-gap h3{margin:0;font-size:13px}.context-gap p{margin:0;color:var(--ui-text-muted);font-size:11px;line-height:1.6}.context-gap button{justify-self:start;border:0;background:transparent;color:var(--ui-accent-ink);padding:2px 0;font-size:11px;font-weight:800;cursor:pointer}
.rail-empty{display:grid;height:100%;align-content:center;justify-items:start;padding:30px}.rail-empty>span{display:grid;width:48px;height:48px;place-items:center;border-radius:12px;background:var(--ui-accent);color:#fff;font-size:17px;font-weight:900}.rail-empty h2{margin:16px 0 9px;font-size:19px;line-height:1.3}.rail-empty p{margin:0;color:var(--ui-text-muted);font-size:12px;line-height:1.65}.rail-empty button{margin-top:17px;border:0;border-radius:9px;background:var(--ui-accent);color:#fff;padding:10px 13px;font-weight:850;cursor:pointer}
button:focus-visible{outline:3px solid var(--ui-focus);outline-offset:2px}
@media (max-width:640px){.context-rail{height:auto;max-height:none;overflow:visible;scrollbar-gutter:auto}.metric-dialog-backdrop{align-items:end;padding:0}.metric-dialog{max-height:88dvh;border-radius:20px 20px 0 0}.metric-dialog-header{grid-template-columns:minmax(0,1fr) auto 34px;padding:18px 16px}.metric-dialog-score strong{font-size:23px}.metric-dialog-body{padding:8px 16px 18px}.metric-dialog>footer{padding:10px 16px}.metric-tooltip{display:none}}
</style>
