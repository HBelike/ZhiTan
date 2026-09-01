import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  observedTurnStatus,
  presentTurnProgress,
  publicTurnError,
  submissionFeedback,
  turnStatusText
} from './career-turn-presentation.js'

test('内部执行步骤折叠为三个用户可理解的进度阶段', () => {
  assert.deepEqual(
    presentTurnProgress({ step: 'validate_input', state: 'completed' }),
    { key: 'preparing', label: '问题和资料已整理', state: 'completed' }
  )
  assert.deepEqual(
    presentTurnProgress({ step: 'build_context', state: 'running' }),
    { key: 'preparing', label: '正在整理问题和资料', state: 'running' }
  )
  assert.deepEqual(
    presentTurnProgress({ step: 'generate_response', state: 'running' }),
    { key: 'analyzing', label: '正在分析并组织回复', state: 'running' }
  )
  assert.deepEqual(
    presentTurnProgress({ step: 'cleanup_temporary_files', state: 'completed' }),
    { key: 'finishing', label: '本轮回复即将完成', state: 'completed' }
  )
})

test('未知步骤不回显服务端提供的内部名称', () => {
  const progress = presentTurnProgress({
    step: 'private_worker_step',
    label: 'Worker 正在执行 private_worker_step',
    state: 'running'
  })

  assert.deepEqual(progress, {
    key: 'processing',
    label: '正在处理你的请求',
    state: 'running'
  })
  assert.doesNotMatch(JSON.stringify(progress), /Worker|private_worker_step/)
})

test('任务状态和排队提示只使用面向用户的自然语言', () => {
  const messages = [
    turnStatusText({ status: 'queued' }),
    turnStatusText({ status: 'running' }),
    submissionFeedback(2),
    observedTurnStatus({ status: 'queued', conversation_position: 2 }),
    observedTurnStatus({ status: 'running' })
  ]

  assert.deepEqual(messages, [
    '消息已收到，正在等待回复',
    '求职助手正在回复，离开或刷新页面也不会中断',
    '消息已收到，前方还有 2 条待回复消息。',
    '正在等待回复，前方还有 2 条消息',
    '正在分析并组织回复…'
  ])
  assert.doesNotMatch(messages.join(' '), /Worker|Turn|服务端|队列/i)
})

test('内部错误转换成可操作的通用提示', () => {
  assert.equal(publicTurnError('Worker lease expired'), '本轮回复未完成，请稍后重试。')
  assert.equal(publicTurnError('职位链接暂时无法读取'), '职位链接暂时无法读取')
  assert.equal(publicTurnError(''), '本轮回复未完成，请稍后重试。')
})

test('求职助手页面不包含面向用户的内部任务术语', () => {
  const pageSource = readFileSync(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')
  const queueSource = readFileSync(new URL('./career-turn-client-queue.js', import.meta.url), 'utf8')

  assert.doesNotMatch(pageSource, /Worker 正在处理|服务端队列|个 Turn|保存到服务端|发送并排队|服务端历史/)
  assert.doesNotMatch(queueSource, /已提交的排队消息/)
})
