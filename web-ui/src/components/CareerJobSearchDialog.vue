<script setup>
import { computed, ref, watch } from 'vue'
import JobSearchWorkspace from './JobSearchWorkspace.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
  currentTargetRole: { type: Object, default: null }
})

const emit = defineEmits(['cancel', 'confirm'])
const selectedJob = ref(null)

const canConfirm = computed(() => Boolean(
  selectedJob.value?.title
  && (selectedJob.value?.company || selectedJob.value?.companyShort)
  && selectedJob.value?.description
  && !props.busy
))

watch(() => props.open, (open) => {
  if (open) selectedJob.value = null
})

function cancel() {
  if (!props.busy) emit('cancel')
}

function confirm() {
  if (canConfirm.value) emit('confirm', selectedJob.value)
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="career-job-dialog-backdrop"
      @mousedown.self="cancel"
      @keydown.esc="cancel"
    >
      <section class="career-job-dialog" role="dialog" aria-modal="true" aria-label="职位检索">
        <header class="career-job-dialog-header">
          <nav aria-label="当前位置"><span>求职助手</span><i>/</i><strong>职位检索</strong></nav>
          <button type="button" aria-label="关闭职位检索" :disabled="busy" @click="cancel">×</button>
        </header>

        <main class="career-job-dialog-body">
          <JobSearchWorkspace selection-mode @selection-change="selectedJob = $event" />
        </main>

        <p v-if="error" class="career-job-dialog-error" role="alert">{{ error }}</p>
        <footer class="career-job-confirmation">
          <div class="selected-job-summary" :class="{ ready: canConfirm || busy }">
            <span class="selected-job-mark" aria-hidden="true">职</span>
            <span v-if="selectedJob">
              <small>当前选择</small>
              <strong>{{ selectedJob.company || selectedJob.companyShort }} · {{ selectedJob.title }}</strong>
            </span>
            <span v-else>
              <small>当前选择</small>
              <strong>请先选择并加载一条完整岗位详情</strong>
            </span>
          </div>
          <p v-if="currentTargetRole">确认后仅影响后续回答，历史消息保持不变。</p>
          <div class="career-job-dialog-actions">
            <button type="button" class="secondary" :disabled="busy" @click="cancel">取消</button>
            <button type="button" class="primary" :disabled="!canConfirm" @click="confirm">
              {{ busy ? '正在更新岗位…' : '确认岗位' }}
            </button>
          </div>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.career-job-dialog-backdrop{position:fixed;z-index:1500;inset:0;display:grid;place-items:center;box-sizing:border-box;background:rgba(8,28,58,.55);padding:24px;backdrop-filter:blur(7px)}
.career-job-dialog{display:grid;width:80vw;height:80dvh;min-height:0;grid-template-rows:auto minmax(0,1fr) auto auto;overflow:hidden;border:1px solid var(--ui-line-strong,#b9cfed);border-radius:22px;background:var(--ui-surface,#fff);box-shadow:0 32px 100px rgba(5,35,78,.3);color:var(--ui-text,#14213d);animation:career-job-dialog-in .16s ease-out}
.career-job-dialog-header{display:flex;align-items:center;justify-content:space-between;gap:24px;border-bottom:1px solid var(--ui-line,#d7e4f6);background:linear-gradient(135deg,var(--ui-surface,#fff),var(--ui-surface-active,#eaf3ff));padding:10px 16px}.career-job-dialog-header nav{display:flex;align-items:center;gap:7px;color:var(--ui-text-muted,#7b879c);font:750 10px/1.3 var(--ui-font-utility,Consolas,monospace)}.career-job-dialog-header nav i{font-style:normal}.career-job-dialog-header nav strong{color:var(--ui-accent-ink,#004aa8)}.career-job-dialog-header>button{display:grid;width:34px;height:34px;place-items:center;border:1px solid var(--ui-line,#d7e4f6);border-radius:10px;background:var(--ui-surface,#fff);color:var(--ui-text-secondary,#526078);font-size:23px;line-height:1;cursor:pointer}.career-job-dialog-header>button:hover:not(:disabled){border-color:var(--ui-accent,#0869d8);color:var(--ui-accent-ink,#004aa8)}
.career-job-dialog-body{display:flex;min-height:0;overflow:hidden;background:var(--ui-canvas,#f3f7fc);padding:14px 16px}.career-job-dialog-body :deep(.job-workspace){width:100%}.career-job-dialog-body :deep(.job-browser){border-radius:12px}
.career-job-dialog-error{margin:0;border-top:1px solid #efcaca;background:#fff4f4;color:#a64141;padding:8px 20px;font-size:12px;font-weight:750}
.career-job-confirmation{display:grid;grid-template-columns:minmax(280px,1fr) minmax(220px,auto) auto;align-items:center;gap:18px;border-top:1px solid var(--ui-line,#d7e4f6);background:var(--ui-surface,#fff);padding:12px 18px}.selected-job-summary{display:flex;min-width:0;align-items:center;gap:10px}.selected-job-summary>span:last-child{display:grid;min-width:0;gap:2px}.selected-job-summary small{color:var(--ui-text-muted,#7b879c);font-size:9px;font-weight:800}.selected-job-summary strong{overflow:hidden;color:var(--ui-text-secondary,#526078);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.selected-job-summary.ready strong{color:var(--ui-text,#14213d)}.selected-job-mark{display:grid;width:34px;height:34px;flex:none;place-items:center;border-radius:11px;background:var(--ui-surface-active,#eaf3ff);color:var(--ui-accent-ink,#004aa8);font-size:12px;font-weight:900}.career-job-confirmation>p{margin:0;color:var(--ui-text-muted,#7b879c);font-size:10px}.career-job-dialog-actions{display:flex;gap:9px}.career-job-dialog-actions button{min-width:104px;min-height:40px;border-radius:10px;padding:8px 15px;font-size:12px;font-weight:850;cursor:pointer}.career-job-dialog-actions .secondary{border:1px solid var(--ui-line-strong,#b9cfed);background:var(--ui-surface,#fff);color:var(--ui-text-secondary,#526078)}.career-job-dialog-actions .primary{border:1px solid var(--ui-accent,#0869d8);background:var(--ui-accent,#0869d8);color:#fff;box-shadow:0 8px 20px color-mix(in srgb,var(--ui-accent,#0869d8) 22%,transparent)}.career-job-dialog-actions button:disabled,.career-job-dialog-header>button:disabled{cursor:not-allowed;opacity:.48}
@keyframes career-job-dialog-in{from{opacity:0}to{opacity:1}}
@media(max-width:900px){.career-job-dialog-backdrop{align-items:end;padding:0}.career-job-dialog{width:100%;height:94dvh;min-height:0;border-radius:20px 20px 0 0}.career-job-dialog-header{padding:13px 14px}.career-job-dialog-body{padding:10px}.career-job-confirmation{grid-template-columns:minmax(0,1fr) auto;padding:10px 12px}.career-job-confirmation>p{display:none}.career-job-dialog-actions .secondary{display:none}.career-job-dialog-actions button{min-width:112px}.career-job-dialog-body :deep(.job-browser){grid-template-columns:1fr}.career-job-dialog-body :deep(.job-detail){display:none}}
@media(prefers-reduced-motion:reduce){.career-job-dialog{animation:none}}
</style>
