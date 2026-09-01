<script setup>
import CareerGreetingDialog from './CareerGreetingDialog.vue'

const candidateProfile = {
  version: 1,
  education: '211 本科',
  experience: '3 年平安银行全栈开发经验'
}

const previewJobs = [
  { id: 'preview-1', title: '前端工程师（React）', company: '钛酷科技', recruiter: '王老板', skills: ['React', 'TypeScript', 'Vite', 'WebView'] },
  { id: 'preview-2', title: '全栈开发工程师', company: '星云智能', recruiter: '李经理', skills: ['Vue', 'Java', '微服务', 'MySQL'] },
  { id: 'preview-3', title: '高级前端工程师', company: '云图数据', recruiter: '陈女士', skills: ['React', '数据可视化', 'ECharts', '工程化'] },
  { id: 'preview-4', title: 'Agent 应用开发', company: '元启科技', recruiter: '赵老板', skills: ['Agent', 'Workflow', 'Python', 'LLM'] },
  { id: 'preview-5', title: 'Web 全栈工程师', company: '半径网络', recruiter: '周经理', skills: ['TypeScript', 'Node.js', 'PostgreSQL', 'Docker'] },
  { id: 'preview-6', title: '前端开发工程师', company: '松果互联', recruiter: '刘女士', skills: ['Vue', '移动端 Web', '性能优化', '组件库'] }
]
</script>

<template>
  <main class="preview-canvas">
    <CareerGreetingDialog
      open
      initial-stage="review"
      simulation-mode
      :candidate-profile="candidateProfile"
      :preview-jobs="previewJobs"
    />
  </main>
</template>

<style scoped>
.preview-canvas { min-height: 100vh; background: #eef4fb; }
</style>
