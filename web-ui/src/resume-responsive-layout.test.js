import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const resumeVue = readFileSync(new URL('./components/ResumeAssistantPage.vue', import.meta.url), 'utf8')

test('简历助手窄容器使用标签切换并保持面板挂载', () => {
  assert.match(resumeVue, /const workspacePane = ref\('assistant'\)/)
  assert.match(resumeVue, /const historyPane = ref\('optimized'\)/)
  assert.match(resumeVue, /role="tablist" aria-label="简历工作区"/)
  assert.match(resumeVue, /:aria-selected="workspacePane === 'document'"/)
  assert.match(resumeVue, /aria-controls="resume-document-pane"/)
  assert.match(resumeVue, /aria-controls="resume-assistant-pane"/)
  assert.doesNotMatch(resumeVue, /v-if="workspacePane ===/)
})

test('简历助手表单和卡片使用自适应网格', () => {
  assert.match(resumeVue, /container:\s*resume-assistant\s*\/\s*inline-size/)
  assert.match(resumeVue, /repeat\(auto-fit,\s*minmax\(220px,\s*1fr\)\)/)
  assert.match(resumeVue, /repeat\(auto-fit,\s*minmax\(240px,\s*1fr\)\)/)
  assert.match(resumeVue, /@container resume-assistant \(max-width:1100px\)/)
  assert.match(resumeVue, /@container resume-assistant \(max-width:900px\)/)
  assert.match(resumeVue, /\.show-workspace-document \.resume-work-canvas > \.document-pane/)
  assert.match(resumeVue, /\.show-workspace-assistant \.resume-work-canvas > \.assistant-pane/)
})
