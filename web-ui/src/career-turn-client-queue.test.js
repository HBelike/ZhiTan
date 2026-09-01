import assert from 'node:assert/strict'
import test from 'node:test'

import {
  firstServerTurn,
  removeServerTurn,
  restoreServerTurns,
  upsertServerTurn
} from './career-turn-client-queue.js'

test('服务端 Turn 按 queue_sequence 恢复且过滤终态', () => {
  const restored = restoreServerTurns([
    { id: 'turn-2', status: 'queued', queue_sequence: 12 },
    { id: 'turn-1', status: 'running', queue_sequence: 11 },
    { id: 'turn-3', status: 'succeeded', queue_sequence: 13 }
  ])

  assert.deepEqual(restored.map((item) => item.id), ['turn-1', 'turn-2'])
  assert.equal(restored[1].content, '等待回复的消息')
})

test('每次 API 受理后立即加入服务端等待列表', () => {
  const first = upsertServerTurn([], {
    id: 'turn-1', status: 'running', queue_sequence: 21, content: '第一条'
  })
  const second = upsertServerTurn(first, {
    id: 'turn-2', status: 'queued', queue_sequence: 22, content: '第二条'
  })

  assert.equal(second.length, 2)
  assert.equal(firstServerTurn(second).id, 'turn-1')
  assert.equal(second[1].content, '第二条')
})

test('状态更新保留本地消息预览并在终态移除', () => {
  const initial = upsertServerTurn([], {
    id: 'turn-1', status: 'queued', queue_sequence: 31,
    content: '优化支付项目', temporaryMessageId: 'local-1'
  })
  const running = upsertServerTurn(initial, {
    id: 'turn-1', status: 'running', queue_sequence: 31
  })

  assert.equal(running[0].content, '优化支付项目')
  assert.equal(running[0].temporaryMessageId, 'local-1')
  assert.deepEqual(removeServerTurn(running, 'turn-1'), [])
  assert.deepEqual(upsertServerTurn(running, {
    id: 'turn-1', status: 'succeeded', queue_sequence: 31
  }), [])
})
