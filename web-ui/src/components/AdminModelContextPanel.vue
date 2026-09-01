<script setup>
import { onMounted, ref } from 'vue'
import { modelProfilesFromAvailability } from '../admin-model-context.js'

const rows = ref([])
const loading = ref(true)
const errorMessage = ref('')

onMounted(loadPolicies)

async function loadPolicies() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await fetch('/api/career/model-profiles', { credentials: 'include', cache: 'no-store' })
    if (!response.ok) throw new Error(await responseError(response, '无法读取模型列表'))
    const profiles = modelProfilesFromAvailability(await response.json())
    rows.value = await Promise.all(profiles.map(async (profile) => {
      const policyResponse = await fetch(
        `/api/admin/career/model-profiles/${encodeURIComponent(profile.profile_key)}/context-policy`,
        { credentials: 'include', cache: 'no-store' }
      )
      if (!policyResponse.ok) throw new Error(await responseError(policyResponse, '无法读取上下文策略'))
      return { ...(await policyResponse.json()).item, saving: false, message: '', error: '' }
    }))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法读取模型上下文策略'
  } finally {
    loading.value = false
  }
}

async function savePolicy(row) {
  row.error = validatePolicy(row)
  row.message = ''
  if (row.error) return
  row.saving = true
  try {
    const response = await fetch(
      `/api/admin/career/model-profiles/${encodeURIComponent(row.profile_key)}/context-policy`,
      {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          context_window_tokens: Number(row.context_window_tokens),
          reserved_output_tokens: Number(row.reserved_output_tokens),
          compression_trigger_percent: Number(row.compression_trigger_percent),
          compression_target_percent: Number(row.compression_target_percent),
          context_window_source: 'admin'
        })
      }
    )
    if (!response.ok) throw new Error(await responseError(response, '保存上下文策略失败'))
    Object.assign(row, (await response.json()).item)
    row.message = '已保存'
  } catch (error) {
    row.error = error instanceof Error ? error.message : '保存上下文策略失败'
  } finally {
    row.saving = false
  }
}

function validatePolicy(row) {
  const windowTokens = Number(row.context_window_tokens)
  const outputTokens = Number(row.reserved_output_tokens)
  const trigger = Number(row.compression_trigger_percent)
  const target = Number(row.compression_target_percent)
  if (!Number.isInteger(windowTokens) || windowTokens < 4096) return '上下文容量不能小于 4096 Token。'
  if (!Number.isInteger(outputTokens) || outputTokens < 1 || outputTokens > windowTokens) return '模型最大输出必须为正数且不能超过上下文容量。'
  if (!Number.isInteger(trigger) || trigger < 50 || trigger > 90) return '压缩触发比例需要在 50% 到 90% 之间。'
  if (!Number.isInteger(target) || target < 30 || target > 75 || target >= trigger) return '压缩目标比例必须低于触发比例。'
  return ''
}

async function responseError(response, fallback) {
  try {
    const payload = await response.json()
    return typeof payload?.detail === 'string' ? payload.detail : fallback
  } catch {
    return fallback
  }
}
</script>

<template>
  <section class="model-context-panel">
    <p v-if="errorMessage" class="context-alert">{{ errorMessage }}</p>
    <p v-if="loading" class="context-loading">正在读取模型上下文策略…</p>
    <div v-else class="context-policy-list">
      <article v-for="row in rows" :key="row.profile_key" class="context-policy-card">
        <header>
          <strong class="context-policy-title">{{ row.profile_key }}</strong>
          <span :class="{ fallback: row.context_window_source === 'fallback' }">
            {{ row.context_window_source === 'fallback' ? '容量待核对' : '已配置' }}
          </span>
        </header>
        <div class="context-field-grid">
          <label><span>上下文容量</span><input v-model.number="row.context_window_tokens" type="number" min="4096" /><small>Token</small></label>
          <label><span>模型最大输出</span><input v-model.number="row.reserved_output_tokens" type="number" min="1" /><small>Token</small></label>
          <label><span>压缩触发比例</span><input v-model.number="row.compression_trigger_percent" type="number" min="50" max="90" /><small>%</small></label>
          <label><span>压缩目标比例</span><input v-model.number="row.compression_target_percent" type="number" min="30" max="75" /><small>%</small></label>
        </div>
        <footer>
          <p v-if="row.error" class="context-row-error">{{ row.error }}</p>
          <p v-else-if="row.message" class="context-row-success">{{ row.message }}</p>
          <span v-else>硬限制固定为 95%，单次回答仍受系统 100000 Token 上限约束</span>
          <button type="button" :disabled="row.saving" @click="savePolicy(row)">{{ row.saving ? '正在保存…' : '保存策略' }}</button>
        </footer>
      </article>
    </div>
  </section>
</template>

<style scoped>
.model-context-panel{display:grid;gap:16px}.context-policy-card{border:1px solid var(--ui-line);border-radius:12px;background:var(--ui-surface);padding:16px}.context-policy-list{display:grid;gap:12px}.context-policy-card header,.context-policy-card footer{display:flex;align-items:center;justify-content:space-between;gap:12px}.context-policy-title{font-size:16px;line-height:1.35;overflow-wrap:anywhere}.context-policy-card header>span{border-radius:999px;background:#e8f5ec;color:#237a43;padding:4px 9px;font-size:12px;white-space:nowrap}.context-policy-card header>span.fallback{background:#fff3d8;color:#8a5b00}.context-field-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0}.context-field-grid label{display:grid;grid-template-columns:1fr auto;gap:5px}.context-field-grid label span{grid-column:1/-1;font-size:13px;font-weight:700}.context-field-grid input{min-width:0;border:1px solid var(--ui-line);border-radius:8px;padding:9px}.context-field-grid small{align-self:center;color:var(--ui-text-muted)}.context-policy-card footer{border-top:1px solid var(--ui-line);padding-top:12px}.context-policy-card footer p,.context-policy-card footer span{margin:0;color:var(--ui-text-muted);font-size:13px}.context-policy-card button{border:0;border-radius:8px;background:var(--ui-accent);color:#fff;padding:9px 14px;font-weight:700}.context-row-error,.context-alert{color:#a72d2d!important}.context-row-success{color:#237a43!important}@media(max-width:900px){.context-field-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
