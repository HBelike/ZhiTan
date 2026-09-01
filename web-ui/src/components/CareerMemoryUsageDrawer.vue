<script setup>
defineProps({ open: Boolean, loading: Boolean, items: { type: Array, default: () => [] } })
const emit = defineEmits(['close'])

const labels = {
  job_intention: '岗位意向', work_experience: '工作经历', education: '学历',
  award: '奖项', publication: '论文', personal_advantage: '个人优势'
}
</script>

<template>
  <div v-if="open" class="memory-drawer-backdrop" @click.self="emit('close')">
    <aside class="memory-drawer" aria-label="本回答使用的求职记忆">
      <header><div><h2>回答依据</h2><p>本回答实际使用的长期求职信息</p></div><button type="button" @click="emit('close')">关闭</button></header>
      <p v-if="loading" class="memory-drawer-empty">正在读取…</p>
      <p v-else-if="!items.length" class="memory-drawer-empty">本回答未使用长期求职记忆。</p>
      <article v-for="item in items" v-else :key="item.memory_id || `${item.memory_type}-deleted`">
        <strong>{{ labels[item.memory_type] || item.memory_type }}</strong>
        <p>{{ item.display_text }}</p>
        <small v-if="item.candidate_profile_name">{{ item.candidate_profile_name }} · v{{ item.candidate_profile_version }}</small>
        <small v-else>来自用户明确说明或修正</small>
      </article>
    </aside>
  </div>
</template>

<style scoped>
.memory-drawer-backdrop{position:fixed;inset:0;z-index:70;background:rgba(25,35,29,.28);display:flex;justify-content:flex-end}.memory-drawer{width:min(440px,92vw);height:100%;overflow:auto;background:#fff;box-shadow:-14px 0 40px rgba(37,58,42,.18);padding:24px}.memory-drawer header{display:flex;justify-content:space-between;gap:16px;border-bottom:1px solid #e8eee3;padding-bottom:16px}.memory-drawer h2,.memory-drawer p{margin:0}.memory-drawer header p,.memory-drawer small{color:#7a8876}.memory-drawer button{border:1px solid #dce6d5;border-radius:9px;background:#fff;padding:7px 11px}.memory-drawer article{margin-top:14px;border:1px solid #e3eadf;border-radius:12px;padding:13px}.memory-drawer article p{margin:7px 0;line-height:1.6}.memory-drawer-empty{margin-top:22px!important;color:#7a8876}
</style>
