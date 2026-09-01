<script setup>
import { computed } from 'vue'
import { contextUsagePresentation, normalizeContextUsage } from '../career-context-usage.js'

const props = defineProps({
  usage: { type: Object, default: null },
  loading: { type: Boolean, default: false }
})

const normalized = computed(() => normalizeContextUsage(props.usage ?? { remaining_percent: 100 }))
const presentation = computed(() => (
  props.loading
    ? { tone: 'compacting', label: '正在估算上下文', spinning: true }
    : contextUsagePresentation(normalized.value)
))
const circumference = 100
const dashOffset = computed(() => circumference - normalized.value.remaining_percent)
</script>

<template>
  <div
    class="career-context-meter"
    :class="[`tone-${presentation.tone}`, { spinning: presentation.spinning }]"
    role="status"
    :aria-label="presentation.label"
    :title="presentation.label"
  >
    <svg viewBox="0 0 40 40" aria-hidden="true">
      <circle class="meter-track" cx="20" cy="20" r="15.9155" />
      <circle
        class="meter-value"
        cx="20"
        cy="20"
        r="15.9155"
        :stroke-dasharray="circumference"
        :stroke-dashoffset="dashOffset"
      />
    </svg>
    <span class="meter-dot" aria-hidden="true"></span>
    <span class="meter-label">{{ presentation.label }}</span>
  </div>
</template>

<style scoped>
.career-context-meter{--meter-color:#5f8f3d;display:inline-flex;height:30px;min-width:30px;align-items:center;gap:5px;color:var(--ui-text-muted)}.career-context-meter svg{width:24px;height:24px;flex:none;transform:rotate(-90deg)}.career-context-meter circle{fill:none;stroke-width:3.2}.meter-track{stroke:var(--ui-line)}.meter-value{stroke:var(--meter-color);stroke-linecap:round;transition:stroke-dashoffset .25s ease}.meter-label{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}.meter-dot{display:none}.tone-warning{--meter-color:#d29024}.tone-compacting{--meter-color:#397bd4}.tone-blocked{--meter-color:#c94747}.spinning svg{animation:context-meter-spin 1s linear infinite}@keyframes context-meter-spin{to{transform:rotate(270deg)}}
</style>
