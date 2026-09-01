import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  createTurnObservationCoordinator,
  shouldReconcileConversationTurn
} from './career-turn-observation.js'

function controllerFactory() {
  return new AbortController()
}

test('切换会话会取消旧 SSE，旧观察器结束时不能清除新观察器', () => {
  const coordinator = createTurnObservationCoordinator({ createController: controllerFactory })
  const first = coordinator.begin('conversation-a', 'turn-a')
  const second = coordinator.begin('conversation-b', 'turn-b')

  assert.equal(first.signal.aborted, true)
  assert.equal(second.signal.aborted, false)
  assert.equal(coordinator.isCurrent(first), false)
  assert.equal(coordinator.isCurrent(second), true)
  assert.equal(coordinator.finish(first), false)
  assert.equal(coordinator.isCurrent(second), true)
  assert.equal(coordinator.finish(second), true)
})

test('离开页面会取消当前 SSE 并使所有晚到回调失效', () => {
  const coordinator = createTurnObservationCoordinator({ createController: controllerFactory })
  const observation = coordinator.begin('conversation-a', 'turn-a')

  coordinator.cancel()

  assert.equal(observation.signal.aborted, true)
  assert.equal(coordinator.isCurrent(observation), false)
  assert.equal(coordinator.finish(observation), false)
})

test('详情仍为活动状态但 active-turns 已为空时需要校准最终消息', () => {
  assert.equal(shouldReconcileConversationTurn({ status: 'queued' }, []), true)
  assert.equal(shouldReconcileConversationTurn({ status: 'running' }, []), true)
  assert.equal(shouldReconcileConversationTurn({ status: 'running' }, [{ id: 'turn-a' }]), false)
  assert.equal(shouldReconcileConversationTurn({ status: 'succeeded' }, []), false)
})

test('求职助手将 SSE 和共享状态绑定到当前会话观察代次', async () => {
  const source = await readFile(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')

  assert.match(source, /createTurnObservationCoordinator/)
  assert.match(source, /signal: observation\.signal/)
  assert.match(source, /observationIsCurrent/)
  assert.match(source, /stopActiveTurnObservation\(\)/)
  assert.match(source, /shouldReconcileConversationTurn/)
  assert.match(source, /submittingTurnCount > 0 \|\| savingJobSearch/)
})
