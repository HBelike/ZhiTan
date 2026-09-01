<script setup>
import { defineAsyncComponent, onBeforeUnmount, onMounted, ref } from 'vue'

const RegionCityPicker = defineAsyncComponent(() => import('v-region').then((module) => module.RegionCityPicker))

const props = defineProps({
  modelValue: { type: Object, required: true },
  cities: { type: Array, default: () => [] },
  hotCities: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false }
})

const emit = defineEmits(['update:model-value'])
const root = ref(null)
const open = ref(false)
const regionCodes = ref([])
const mappingError = ref('')

function normalizedCityName(value) {
  return String(value ?? '')
    .trim()
    .replace(/特别行政区$/, '')
    .replace(/(市|地区|盟|自治州)$/, '')
}

function findBossCity(name) {
  const target = normalizedCityName(name)
  return props.cities.find((city) => normalizedCityName(city.name) === target)
}

function toggle() {
  open.value = !open.value
  mappingError.value = ''
}

function close() {
  open.value = false
  mappingError.value = ''
}

function choose(city) {
  emit('update:model-value', city)
  close()
}

function handleRegionChange(items) {
  const values = Array.isArray(items) ? items : []
  const selected = values.at(-1)
  if (!selected) return

  const city = findBossCity(selected.value)
  if (!city) {
    mappingError.value = `“${selected.value}”暂不在当前招聘城市目录中`
    regionCodes.value = []
    return
  }

  regionCodes.value = [selected.key]
  choose(city)
}

function handleOutside(event) {
  if (open.value && root.value && !root.value.contains(event.target)) close()
}

onMounted(() => document.addEventListener('pointerdown', handleOutside))
onBeforeUnmount(() => document.removeEventListener('pointerdown', handleOutside))
</script>

<template>
  <div ref="root" class="city-picker" @keydown.esc.stop="close">
    <button
      type="button"
      class="city-picker-trigger"
      :aria-expanded="open"
      aria-haspopup="dialog"
      @click="toggle"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 21s6-5.2 6-11a6 6 0 1 0-12 0c0 5.8 6 11 6 11Z"></path>
        <circle cx="12" cy="10" r="2"></circle>
      </svg>
      <span><small>工作城市</small><strong>{{ modelValue.name }}</strong></span>
      <i aria-hidden="true"></i>
    </button>

    <Transition name="city-panel">
      <section v-if="open" class="city-picker-panel" role="dialog" aria-label="选择工作城市">
        <header>
          <div>
            <small>职位搜索范围</small>
            <h3>选择工作城市</h3>
            <p>支持城市名搜索，也可以按省份浏览</p>
          </div>
          <button type="button" aria-label="关闭城市选择" @click="close">×</button>
        </header>

        <div class="hot-city-section">
          <div class="section-heading">
            <strong>热门城市</strong>
            <span v-if="loading">正在同步招聘城市…</span>
          </div>
          <div class="hot-city-list">
            <button
              v-for="city in hotCities"
              :key="city.code"
              type="button"
              :class="{ active: city.code === modelValue.code }"
              @click="choose(city)"
            >{{ city.name }}</button>
          </div>
        </div>

        <div class="open-source-city-picker">
          <div class="source-note">
            <strong>全部城市</strong>
            <span>输入城市名称快速查找</span>
          </div>
          <RegionCityPicker
            v-model="regionCodes"
            language="cn"
            @change="handleRegionChange"
          />
          <p v-if="mappingError" class="mapping-error">{{ mappingError }}</p>
        </div>

        <footer>
          <span>城市目录由开源组件 v-region 提供</span>
          <span>招聘城市代码以当前 BOSS 会话为准</span>
        </footer>
      </section>
    </Transition>
  </div>
</template>

<style scoped>
.city-picker { position: relative; z-index: 12; }
.city-picker-trigger { display: grid; min-width: 132px; min-height: 42px; grid-template-columns: 18px minmax(0, 1fr) 9px; align-items: center; gap: 9px; border: 1px solid var(--job-line-strong, #b9cfed); border-radius: 10px; background: var(--job-paper, #fff); color: var(--job-ink, #14213d); padding: 5px 10px; cursor: pointer; text-align: left; transition: border-color .16s ease, box-shadow .16s ease; }
.city-picker-trigger:hover,.city-picker-trigger[aria-expanded=true] { border-color: var(--job-blue, #0869d8); box-shadow: 0 0 0 3px var(--ui-focus, rgba(8, 105, 216, .13)); }
.city-picker-trigger svg { width: 18px; fill: none; stroke: var(--job-blue, #0869d8); stroke-width: 1.8; }
.city-picker-trigger > span { display: grid; min-width: 0; gap: 1px; }
.city-picker-trigger small { color: var(--job-muted, #7b879c); font-size: 8px; font-weight: 700; }
.city-picker-trigger strong { overflow: hidden; font-size: 12px; font-weight: 820; text-overflow: ellipsis; white-space: nowrap; }
.city-picker-trigger > i { width: 7px; height: 7px; border-right: 1.5px solid var(--job-copy, #526078); border-bottom: 1.5px solid var(--job-copy, #526078); transform: rotate(45deg) translateY(-2px); transition: transform .16s ease; }
.city-picker-trigger[aria-expanded=true] > i { transform: rotate(225deg) translate(-2px, -1px); }

.city-picker-panel { position: absolute; z-index: 30; top: calc(100% + 10px); right: 0; display: flex; width: min(720px, calc(100vw - 80px)); max-height: min(650px, calc(100vh - 210px)); flex-direction: column; overflow: hidden; border: 1px solid var(--job-line-strong, #b9cfed); border-radius: 16px; background: var(--job-paper, #fff); box-shadow: 0 24px 70px rgba(13, 52, 102, .22); color: var(--job-ink, #14213d); }
.city-picker-panel > header { display: flex; align-items: center; justify-content: space-between; gap: 18px; background: linear-gradient(135deg, var(--job-blue, #0869d8), #2382e7); color: #fff; padding: 15px 18px; }
.city-picker-panel > header small { display: block; font-size: 8px; font-weight: 750; opacity: .72; }
.city-picker-panel > header h3 { margin: 3px 0 0; font: 850 17px/1.2 var(--ui-font-display, "Segoe UI Variable Display", sans-serif); }
.city-picker-panel > header p { margin: 4px 0 0; font-size: 9px; opacity: .72; }
.city-picker-panel > header button { display: grid; width: 30px; height: 30px; place-items: center; border: 1px solid rgba(255, 255, 255, .32); border-radius: 9px; background: rgba(255, 255, 255, .1); color: #fff; cursor: pointer; font-size: 21px; }

.hot-city-section { border-bottom: 1px solid var(--job-line, #d7e4f6); padding: 14px 18px 13px; }
.section-heading,.source-note { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.section-heading strong,.source-note strong { color: var(--job-copy, #526078); font-size: 10px; font-weight: 850; }
.section-heading span,.source-note span { color: var(--job-muted, #7b879c); font-size: 8px; }
.hot-city-list { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 7px; margin-top: 9px; }
.hot-city-list button { border: 1px solid transparent; border-radius: 7px; background: var(--job-canvas, #f3f7fc); color: var(--job-copy, #526078); padding: 7px 5px; cursor: pointer; font-size: 10px; }
.hot-city-list button:hover { border-color: var(--job-line-strong, #b9cfed); background: var(--job-blue-soft, #eaf3ff); color: var(--job-blue-ink, #004aa8); }
.hot-city-list button.active { border-color: var(--job-blue, #0869d8); background: var(--job-blue, #0869d8); color: #fff; font-weight: 850; }

.open-source-city-picker { min-height: 0; flex: 1; overflow: auto; padding: 12px 18px 14px; }
.source-note { margin-bottom: 8px; }
.open-source-city-picker :deep(.rg-city-picker) { width: 100%; color: var(--job-ink, #14213d); }
.open-source-city-picker :deep(.rg-search-bar) { margin: 0 0 10px; }
.open-source-city-picker :deep(.rg-search-input) { border: 1px solid var(--job-line, #d7e4f6); border-radius: 9px; background: var(--job-blue-soft, #eaf3ff); margin-right: 0; }
.open-source-city-picker :deep(.rg-search-input:focus-within) { border-color: var(--job-blue, #0869d8); box-shadow: 0 0 0 3px var(--ui-focus, rgba(8, 105, 216, .13)); }
.open-source-city-picker :deep(.rg-search-input input) { min-height: 36px; color: var(--job-ink, #14213d); font-family: inherit; font-size: 11px; }
.open-source-city-picker :deep(.rg-picker) { width: auto; max-height: 330px; padding: 0 3px 5px; row-gap: 10px; }
.open-source-city-picker :deep(.rg-picker__province) { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 10px; border-bottom: 1px dashed var(--job-line, #d7e4f6); padding: 8px 0; }
.open-source-city-picker :deep(.rg-picker__title) { margin: 0; color: var(--job-blue, #0869d8); font-size: 11px; font-weight: 850; }
.open-source-city-picker :deep(.rg-picker__body) { gap: 5px 8px; }
.open-source-city-picker :deep(.rg-picker__city) { border-radius: 6px; color: var(--job-copy, #526078); padding: 4px 6px; font-size: 10px; }
.open-source-city-picker :deep(.rg-picker__city:hover) { background: var(--job-blue-soft, #eaf3ff); color: var(--job-blue-ink, #004aa8); }
.open-source-city-picker :deep(.rg-picker__city.selected) { background: var(--job-blue, #0869d8); color: #fff; }
.mapping-error { margin: 9px 0 0; border-radius: 7px; background: #fff4e5; color: #8c621c; padding: 8px 10px; font-size: 9px; }
.city-picker-panel > footer { display: flex; align-items: center; justify-content: space-between; gap: 20px; border-top: 1px solid var(--job-line, #d7e4f6); background: color-mix(in srgb, var(--job-canvas, #f3f7fc) 72%, #fff); color: var(--job-muted, #7b879c); padding: 8px 18px; font-size: 8px; }

.city-panel-enter-active,.city-panel-leave-active { transition: opacity .14s ease, transform .14s ease; }
.city-panel-enter-from,.city-panel-leave-to { opacity: 0; transform: translateY(-5px); }
@media(prefers-reduced-motion: reduce) { .city-picker-trigger,.city-picker-trigger > i,.city-panel-enter-active,.city-panel-leave-active { transition: none; } }
</style>
