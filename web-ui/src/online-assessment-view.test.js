import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { normalizeAppRoute } from './navigation-access.js'
import { ONLINE_ASSESSMENT_PATH, openOnlineAssessmentAssistant } from './online-assessment/launcher.js'

test('online assessment has a dedicated career route and named window', () => {
  assert.equal(normalizeAppRoute('/career/online-assessment'), ONLINE_ASSESSMENT_PATH)
  let opened = null
  const result = openOnlineAssessmentAssistant({
    origin: 'https://example.test',
    openWindow(url, name, features) {
      opened = { url, name, features }
      return { focus() {} }
    }
  })
  assert.equal(result, true)
  assert.equal(new URL(opened.url).pathname, '/career/online-assessment')
  assert.equal(opened.name, 'career-online-assessment')
})

test('online assessment page exposes re-capture, tests and interview archive actions', () => {
  const page = readFileSync(new URL('./components/OnlineAssessmentAssistantPage.vue', import.meta.url), 'utf8')
  const app = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')

  assert.match(page, /重新识别/)
  assert.match(page, /补充题面/)
  assert.match(page, /重新测试/)
  assert.match(page, /保存到面经库/)
  assert.match(page, /不自动填写或提交/)
  assert.match(page, /下载最新版扩展/)
  assert.match(page, /重新检测/)
  assert.match(app, /OnlineAssessmentAssistantPage/)
})

test('admin console exposes independent visibility switches for both career tools', () => {
  const adminPage = readFileSync(new URL('./components/AdminConsolePage.vue', import.meta.url), 'utf8')
  const careerPage = readFileSync(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')
  const app = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')

  assert.match(adminPage, /求职助手子功能/)
  assert.match(adminPage, /两个开关分别控制对应入口/)
  assert.match(careerPage, /interviewMasterVisible/)
  assert.match(careerPage, /onlineAssessmentVisible/)
  assert.match(app, /career_interview_master/)
  assert.match(app, /career_online_assessment/)
})
