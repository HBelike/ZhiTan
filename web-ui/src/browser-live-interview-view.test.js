import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  advanceSetupProgress,
  buildArchivePreview,
  defaultInterviewDate,
  estimateAsrCost,
  formatDuration,
  isNearScrollEnd,
  isInterviewMasterPath,
  pickInitialAnswerModel
} from './browser-live-interview/view.js'

const interviewMasterPage = readFileSync(
  new URL('./components/BrowserInterviewMasterPage.vue', import.meta.url),
  'utf8'
)
const interviewLibraryPage = readFileSync(
  new URL('./components/InterviewLibraryPage.vue', import.meta.url),
  'utf8'
)

test('ending an interview exposes the optional archive dialog contract', () => {
  for (const copy of ['保存本次面经？', '公司名称', '面试职位', '面试日期', '本次不保存', '保存到面经库']) {
    assert.match(interviewMasterPage, new RegExp(copy))
  }
  assert.ok(
    interviewMasterPage.indexOf('await currentCapture?.stop()') < interviewMasterPage.indexOf('await openArchiveDialog()'),
    '声音采集必须先停止，之后才能打开归档弹窗'
  )
})

test('saved archive links can open the matching interview library entry', () => {
  assert.match(interviewMasterPage, /\/interviews\?experience_id=/)
  assert.match(interviewLibraryPage, /new URLSearchParams\(window\.location\.search\)\.get\('experience_id'\)/)
})

test('archive preview uses complete server count and only shows five questions', () => {
  const preview = buildArchivePreview({
    question_count: 8,
    question_preview: ['问题1', '问题2', '问题3', '问题4', '问题5', '问题6'],
    started_at: '2026-08-24T06:30:00Z'
  })

  assert.equal(preview.questionCount, 8)
  assert.deepEqual(preview.questions, ['问题1', '问题2', '问题3', '问题4', '问题5'])
  assert.equal(preview.remainingCount, 3)
  assert.equal(preview.interviewDate, '2026-08-24')
})

test('archive date falls back to the local current date', () => {
  assert.equal(defaultInterviewDate('2026-08-24T06:30:00Z'), '2026-08-24')
  assert.match(defaultInterviewDate('', new Date(2026, 7, 25, 9, 0, 0)), /^2026-08-25$/)
})

test('transcript list only follows when the reader stays near the latest message', () => {
  assert.equal(isNearScrollEnd({ scrollHeight: 1000, scrollTop: 650, clientHeight: 320 }), true)
  assert.equal(isNearScrollEnd({ scrollHeight: 1000, scrollTop: 300, clientHeight: 320 }), false)
})

test('setup progress advances smoothly without claiming completion', () => {
  assert.equal(advanceSetupProgress(24), 30)
  assert.equal(advanceSetupProgress(91), 92)
  assert.equal(advanceSetupProgress(92), 92)
})

test('interview master view recognizes its single-task route', () => {
  assert.equal(isInterviewMasterPath('/career/interview-master'), true)
  assert.equal(isInterviewMasterPath('/career'), false)
})

test('interview master view explains the supported desktop browsers', () => {
  assert.match(interviewMasterPage, /Chrome、Edge 或 Firefox/)
  assert.doesNotMatch(interviewMasterPage, /Chrome 电脑声音实时辅助/)
})

test('interview master exposes the hidden viewport V1 browser isolation contract', () => {
  for (const copy of [
    '隐藏视口V1',
    '仅分享指定标签页或窗口时生效',
    '分享整个屏幕时仍会显示',
    '回答与问题已移至独立小窗',
    '面试开始且实时连接就绪后可开启'
  ]) {
    assert.match(interviewMasterPage, new RegExp(copy))
  }
  assert.match(interviewMasterPage, /hiddenViewportEnabled/)
  assert.match(interviewMasterPage, /toggleHiddenViewport/)
  assert.match(interviewMasterPage, /hidden-viewport-stage/)
  assert.match(interviewMasterPage, /connectionState\.value === 'ready'/)
  assert.match(interviewMasterPage, /await createAndConnectLiveSession/)
  assert.match(interviewMasterPage, /正在保留声音来源并自动重试/)
  assert.match(interviewMasterPage, /generation === startGeneration/)
})

test('interview master view formats duration and Qwen estimate', () => {
  assert.equal(formatDuration(125), '02:05')
  assert.equal(estimateAsrCost(3600, 1), 1.188)
  assert.equal(estimateAsrCost(3600, 2), 2.376)
})

test('interview master view inherits a ready answer model only', () => {
  const models = [
    { id: 'blocked', readiness: 'blocked' },
    { id: 'ready-1', readiness: 'ready' }
  ]
  assert.equal(pickInitialAnswerModel(models, 'blocked'), 'ready-1')
  assert.equal(pickInitialAnswerModel(models, 'ready-1'), 'ready-1')
})
