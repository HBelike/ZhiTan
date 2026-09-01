import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  getCareerLayoutMode,
  getPanelAfterLayoutChange
} from './career-responsive-layout.js'

const careerPageVue = readFileSync(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')

test('求职助手响应式模式在容器边界稳定切换', () => {
  assert.equal(getCareerLayoutMode(819), 'single')
  assert.equal(getCareerLayoutMode(820), 'compact')
  assert.equal(getCareerLayoutMode(1239), 'compact')
  assert.equal(getCareerLayoutMode(1240), 'wide')
  assert.equal(getPanelAfterLayoutChange('compact', 'compact', 'context'), 'context')
  assert.equal(getPanelAfterLayoutChange('compact', 'wide', 'context'), '')
})

test('求职助手响应式抽屉复用现有历史与上下文面板', () => {
  assert.match(careerPageVue, /ref="careerWorkspaceRef"/)
  assert.match(careerPageVue, /compactPanel/)
  assert.match(careerPageVue, /aria-controls="career-history-panel"/)
  assert.match(careerPageVue, /aria-controls="career-context-panel"/)
  assert.match(careerPageVue, /class="career-panel-scrim"/)
  assert.match(careerPageVue, /event\.key !== 'Escape'/)
  assert.match(careerPageVue, /@container app-main \(max-width:1239px\)/)
  assert.match(careerPageVue, /@container app-main \(max-width:819px\)/)
  assert.doesNotMatch(careerPageVue, /calc\(100vw - 810px\)|calc\(100vw - 578px\)/)
})
