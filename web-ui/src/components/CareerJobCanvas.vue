<script setup>
import { computed } from 'vue'
import { JOB_SECTION_RULES, isJobSubheadingLine, parseJobCanvasText } from '../job-canvas-parser.js'
import { assessmentCanvasSections, isAssessmentPending, sectionItemNumber } from '../career-assessment-view.js'

const props = defineProps({
  targetRole: { type: Object, required: true },
  metrics: { type: Array, default: () => [] },
  assessment: { type: Object, default: null },
  candidateProfile: { type: Object, default: null },
  wide: { type: Boolean, default: false }
})

const emit = defineEmits(['select-metric', 'retry-assessment'])
const parsed=computed(()=>parseJobCanvasText(props.targetRole.job_text,{
  companyName:props.targetRole.company_name,
  roleName:props.targetRole.role_name,
  requirements:props.targetRole.requirements
}))
const judgeSections=computed(()=>assessmentCanvasSections(props.assessment))
const hasStructuredCanvas=computed(()=>judgeSections.value?.hasStructure||parsed.value.hasReliableStructure)
const responsibilities=computed(()=>judgeSections.value?.responsibilities??parsed.value.sections.responsibilities??[])
const requirementEntries=computed(()=>judgeSections.value?.requirements??(parsed.value.sections.requirements??[]).filter((item)=>!isJobSubheadingLine(item)).map((text)=>({text,type:requirementType(text)})))
const supportingSections=computed(()=>judgeSections.value?.supporting??JOB_SECTION_RULES.filter((rule)=>!['responsibilities','requirements'].includes(rule.key)).map((rule)=>({...rule,items:parsed.value.sections[rule.key]??[]})).filter((section)=>section.items.length))
function formatMetricScore(metric) { return metric?.status==='ready'&&metric.score!==null?`${metric.score}%`:'待分析' }
function requirementType(item) {
  const normalized=item.replace(/\s+/g,'')
  const matched=props.targetRole.requirements?.find((requirement)=>{ const text=String(requirement.text??'').replace(/\s+/g,''); return text&&(text.includes(normalized)||normalized.includes(text)) })
  if (matched?.priority==='bonus') return '加分'
  if (/学历|经验|年限/.test(item)) return '经验'
  return '必须'
}
</script>

<template>
  <article class="job-canvas" :class="{ wide }" aria-label="结构化职位原文">
    <section v-if="isAssessmentPending(assessment)" class="job-analysis-state is-running" aria-live="polite">
      <span class="analysis-pulse" aria-hidden="true"></span>
      <div><strong>{{ assessment.status === 'queued' ? '岗位分析已排队' : '正在分析岗位与简历' }}</strong><small>完成后会自动更新岗位维度和证据。</small></div>
    </section>
    <section v-else-if="assessment?.status === 'failed'" class="job-analysis-state is-failed" role="status">
      <div><strong>岗位分析失败</strong><small>模型和基础规则均未形成可信结果。</small></div>
      <button type="button" @click="emit('retry-assessment')">重新分析</button>
    </section>
    <section v-if="metrics.length" class="job-match-summary" aria-label="职业匹配度分析">
      <header>
        <div><h3>职业匹配度分析</h3><span v-if="assessment?.status === 'fallback_ready'">基础规则分析</span></div>
        <small v-if="candidateProfile">基于 {{ candidateProfile.display_name }}</small>
      </header>
      <div class="job-match-grid">
        <button
          v-for="metric in metrics"
          :key="metric.key"
          type="button"
          :class="metric.key"
          :aria-label="`${metric.short} ${formatMetricScore(metric)}，查看评分依据`"
          @click="emit('select-metric',metric.key)"
        >
          <span>{{ metric.short }}</span>
          <strong>{{ formatMetricScore(metric) }}</strong>
          <small v-if="metric.status==='ready'">{{ metric.item_count ? `${metric.item_count} 项分析依据` : '查看分析依据' }}</small>
          <small v-else>资料补充后计算</small>
          <em>{{ metric.description }}</em>
        </button>
      </div>
    </section>

    <section class="job-hero">
      <div class="job-title-line">
        <span v-if="targetRole.company_name" class="job-company">{{ targetRole.company_name }}</span>
        <i v-if="targetRole.company_name" aria-hidden="true"></i>
        <h3>{{ targetRole.role_name }}</h3>
      </div>
      <div v-if="parsed.meta.length" class="job-meta" aria-label="岗位基本信息">
        <span v-for="item in parsed.meta" :key="item.label" :class="{ emphasis:item.emphasis }" :title="item.label">{{ item.value }}</span>
      </div>
    </section>

    <section v-if="!judgeSections && parsed.skills.length" class="job-section job-profile">
      <div class="skill-strip"><span v-for="skill in parsed.skills" :key="skill">{{ skill }}</span></div>
    </section>

    <div v-if="hasStructuredCanvas" class="job-main-grid">
      <section class="job-section job-primary-section">
        <header><div><span>职位职责</span><h4>你会负责什么</h4></div><small>{{ responsibilities.length }} 项</small></header>
        <ol v-if="responsibilities.length" class="job-points">
          <li v-for="(item,index) in responsibilities.slice(0,4)" :key="`responsibility-${index}`" :class="{ 'is-single': !sectionItemNumber(responsibilities,index) }"><i v-if="sectionItemNumber(responsibilities,index)">{{ sectionItemNumber(responsibilities,index) }}</i><p>{{ item }}</p></li>
        </ol>
        <p v-else class="job-empty">原文没有识别出独立的职责分区。</p>
        <details v-if="responsibilities.length>4" class="inline-details"><summary>查看其余 {{ responsibilities.length-4 }} 项职责</summary><ul><li v-for="(item,index) in responsibilities.slice(4)" :key="`responsibility-more-${index}`">{{ item }}</li></ul></details>
      </section>

      <section class="job-section job-primary-section">
        <header><div><span>任职要求</span><h4>核心门槛</h4></div><small>优先展示前 4 项</small></header>
        <div v-if="requirementEntries.length" class="job-requirements">
          <article v-for="(item,index) in requirementEntries.slice(0,4)" :key="`requirement-${index}`" :class="item.type"><i></i><div><small><b v-if="sectionItemNumber(requirementEntries,index)">{{ sectionItemNumber(requirementEntries,index) }}</b>{{ item.type }}</small><p>{{ item.text }}</p></div></article>
        </div>
        <p v-else class="job-empty">原文没有识别出独立的任职要求分区。</p>
        <details v-if="requirementEntries.length>4" class="inline-details"><summary>查看其余 {{ requirementEntries.length-4 }} 项要求</summary><ul><li v-for="(item,index) in requirementEntries.slice(4)" :key="`requirement-more-${index}`">{{ item.text }}</li></ul></details>
      </section>
    </div>

    <section v-else class="job-section job-raw-fallback">
      <header><div><span>职位原文</span><h4>原始职位描述</h4></div><small>未可靠识别分区，按原文展示</small></header>
      <pre>{{ parsed.rawJobDescription || targetRole.job_text }}</pre>
    </section>

    <template v-if="hasStructuredCanvas">
      <details v-for="section in supportingSections" :key="section.key" class="job-details"><summary><span>{{ section.label }}</span><small>{{ section.items.length }} 项</small></summary><ul><li v-for="(item,index) in section.items" :key="`${section.key}-${index}`">{{ item }}</li></ul></details>
      <details v-if="parsed.other.length" class="job-details"><summary><span>其他职位信息</span><small>{{ parsed.other.length }} 条</small></summary><ul><li v-for="(item,index) in parsed.other" :key="`other-${index}`">{{ item }}</li></ul></details>
    </template>

    <div class="job-integrity-note">
      <span v-if="hasStructuredCanvas">已从原文整理 {{ responsibilities.length + requirementEntries.length }} 条职责与要求</span>
      <span v-else>职位信息按 JD 原文展示</span>
      <small>{{ hasStructuredCanvas ? '仅重排原文，不补写信息' : '不推断、不补写' }}</small>
    </div>
  </article>
</template>

<style scoped>
.job-canvas{overflow:hidden;border:1px solid var(--ui-line);border-radius:18px;background:var(--ui-surface);color:var(--ui-text);box-shadow:0 14px 38px rgba(35,71,112,.07)}.job-hero,.job-section{padding:18px 20px}.job-analysis-state{display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid var(--ui-line);background:var(--ui-surface-soft);padding:14px 20px}.job-analysis-state>div{display:grid;gap:3px}.job-analysis-state strong{font-size:12px}.job-analysis-state small{color:var(--ui-text-muted);font-size:9px}.job-analysis-state button{border:1px solid var(--ui-line-strong);border-radius:8px;background:var(--ui-surface);color:var(--ui-accent-ink);padding:7px 10px;font-size:10px;font-weight:850;cursor:pointer}.analysis-pulse{width:10px;height:10px;flex:none;border:3px solid var(--ui-line-strong);border-top-color:var(--ui-accent);border-radius:50%;animation:analysis-spin .8s linear infinite}.is-running>div{margin-right:auto}.is-failed{border-color:#edcccc;background:#fff7f7}.job-match-summary{position:relative;z-index:2;padding:20px;background:linear-gradient(135deg,#2476cf 0%,#2783df 58%,#61a4ef 100%);color:#fff}.job-match-summary>header{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px}.job-match-summary>header>div{display:flex;align-items:center;gap:8px}.job-match-summary>header h3{margin:0;font:900 20px/1.25 var(--ui-font-display)}.job-match-summary>header span{border:1px solid rgba(255,255,255,.45);border-radius:999px;padding:3px 6px;font-size:8px;font-weight:800}.job-match-summary>header small{max-width:42%;overflow:hidden;font-size:9px;font-weight:750;text-overflow:ellipsis;white-space:nowrap;opacity:.78}.job-match-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}.job-match-grid button{position:relative;display:grid;min-width:0;gap:4px;border:1px solid rgba(255,255,255,.34);border-radius:12px;background:rgba(255,255,255,.96);box-shadow:0 7px 18px rgba(0,48,112,.16);color:var(--ui-text);padding:12px 13px;text-align:left;cursor:pointer;transition:transform .15s ease,box-shadow .15s ease}.job-match-grid button:hover,.job-match-grid button:focus-visible{transform:translateY(-2px);box-shadow:0 11px 24px rgba(0,39,94,.24);outline:3px solid rgba(255,255,255,.5);outline-offset:1px}.job-match-grid span{overflow:hidden;color:var(--ui-text-secondary);font-size:9px;font-weight:850;text-overflow:ellipsis;white-space:nowrap}.job-match-grid strong{color:#075fc9;font:900 24px/1.05 var(--ui-font-display);letter-spacing:-.035em}.job-match-grid .critical_gap strong{color:var(--ui-danger,#b54747)}.job-match-grid small{color:var(--ui-text-muted);font-size:8px;font-weight:700}.job-match-grid em{position:absolute;z-index:6;top:calc(100% + 7px);right:0;display:none;width:190px;border-radius:9px;background:var(--ui-text);box-shadow:0 10px 24px rgba(14,38,68,.24);color:var(--ui-surface);padding:9px;font-size:9px;font-style:normal;line-height:1.5}.job-match-grid button:hover em,.job-match-grid button:focus-visible em{display:block}.job-hero{background:linear-gradient(145deg,var(--ui-surface-active),var(--ui-surface) 70%)}.job-title-line{display:flex;align-items:baseline;flex-wrap:wrap;gap:9px}.job-title-line>i{width:1px;height:15px;background:var(--ui-line-strong);transform:translateY(2px)}.job-company{color:var(--ui-accent-ink);font-size:11px;font-weight:850}.job-hero h3{margin:0;font:850 21px/1.35 var(--ui-font-display)}.job-meta{display:flex;flex-wrap:wrap;gap:7px;margin-top:14px}.job-meta span{border:1px solid var(--ui-line);border-radius:999px;background:var(--ui-surface);color:var(--ui-text-secondary);padding:6px 9px;font-size:10px;font-weight:800}.job-meta span.emphasis{border-color:var(--ui-accent);background:var(--ui-accent);color:#fff}@keyframes analysis-spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.analysis-pulse{animation:none}}
.job-section{border-top:1px solid var(--ui-line)}.job-section>header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:13px}.job-section>header span{display:block;color:var(--ui-text-muted);font-size:9px;font-weight:800}.job-section h4{margin:3px 0 0;font:850 14px/1.35 var(--ui-font-display)}.job-section>header>small{color:var(--ui-text-muted);font-size:9px;font-weight:750;text-align:right}.skill-strip{display:flex;flex-wrap:wrap;gap:6px}.skill-strip span{border-radius:999px;background:var(--ui-surface-soft);color:var(--ui-accent-ink);padding:6px 9px;font-size:9px;font-weight:800}
.job-main-grid{display:grid}.job-primary-section{min-width:0}.job-points{display:grid;gap:11px;margin:0;padding:0;list-style:none}.job-points li{display:grid;grid-template-columns:24px minmax(0,1fr);align-items:start;gap:9px}.job-points i{display:grid;width:24px;height:24px;place-items:center;border-radius:50%;background:var(--ui-surface-active);color:var(--ui-accent-ink);font-size:9px;font-style:normal;font-weight:900}.job-points p,.job-requirements p{margin:0;color:var(--ui-text-secondary);font-size:10px;line-height:1.65;white-space:pre-line}.job-requirements{display:grid;gap:9px}.job-requirements article{display:grid;grid-template-columns:6px minmax(0,1fr);gap:10px;border-radius:10px;background:var(--ui-surface-soft);padding:10px 11px}.job-requirements article>i{border-radius:999px;background:var(--ui-accent)}.job-requirements article.经验>i{background:var(--ui-success,#247a5a)}.job-requirements article.加分>i{background:var(--ui-warning,#9a650d)}.job-requirements article small{display:block;margin-bottom:3px;color:var(--ui-text-muted);font-size:8px;font-weight:850}.job-empty{margin:0;color:var(--ui-text-muted);font-size:10px;line-height:1.6}.job-raw-fallback pre{max-height:560px;overflow:auto;margin:0;border:1px solid var(--ui-line);border-radius:12px;background:var(--ui-surface-soft);color:var(--ui-text-secondary);padding:15px 16px;font:400 10px/1.75 var(--ui-font-body);white-space:pre-wrap;word-break:break-word}
.job-points li.is-single{grid-template-columns:minmax(0,1fr)}.job-requirements article small{display:flex;align-items:center;gap:4px}.job-requirements article small b{display:inline-grid;min-width:14px;height:14px;place-items:center;border-radius:50%;background:var(--ui-surface-active);color:var(--ui-accent-ink);font-size:7px}
.inline-details{margin-top:12px;border-top:1px dashed var(--ui-line)}.inline-details summary{padding:10px 0 0;color:var(--ui-accent-ink);font-size:9px;font-weight:850;cursor:pointer}.inline-details ul,.job-details ul{display:grid;gap:8px;margin:10px 0 0;padding:0 0 0 18px;color:var(--ui-text-secondary);font-size:10px;line-height:1.65}.job-details{border-top:1px solid var(--ui-line)}.job-details summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 20px;color:var(--ui-text);font-size:11px;font-weight:850;cursor:pointer;list-style:none}.job-details summary::-webkit-details-marker{display:none}.job-details summary:after{order:3;color:var(--ui-accent-ink);content:"＋"}.job-details[open] summary:after{content:"－"}.job-details summary small{margin-left:auto;color:var(--ui-text-muted);font-size:9px}.job-details ul{margin:0;padding:0 38px 16px}
.job-integrity-note{display:flex;align-items:center;justify-content:space-between;gap:8px;border-top:1px solid var(--ui-line);background:var(--ui-surface-soft);color:var(--ui-text-muted);padding:9px 20px;font-size:8px}.job-integrity-note small{font-size:8px;white-space:nowrap}
.job-canvas.wide .job-main-grid{grid-template-columns:1fr 1fr}.job-canvas.wide .job-primary-section+.job-primary-section{border-left:1px solid var(--ui-line)}.job-canvas.wide .job-hero h3{font-size:24px}.job-canvas.wide .job-points p,.job-canvas.wide .job-requirements p{font-size:11px}@media(max-width:640px){.job-match-summary,.job-hero,.job-section{padding:16px}.job-match-summary>header{align-items:flex-start;flex-direction:column;gap:6px}.job-match-summary>header small{max-width:100%}.job-match-grid{grid-template-columns:1fr 1fr}.job-match-grid strong{font-size:21px}.job-hero h3{font-size:19px}.job-details summary{padding:14px 16px}.job-details ul{padding:0 30px 16px}.job-integrity-note{padding:9px 16px}.job-match-grid em{display:none!important}}
</style>
