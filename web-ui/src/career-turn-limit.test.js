import test from 'node:test'
import assert from 'node:assert/strict'

import { normalizeTurnLimit, turnLimitPresentation } from './career-turn-limit.js'

test('剩余两轮和一轮时显示温和提醒', () => {
  assert.deepEqual(turnLimitPresentation({ remaining_turns: 2, max_turns: 30 }), {
    blocked: false,
    message: '本对话还可继续 2 轮。'
  })
  assert.equal(turnLimitPresentation({ remaining_turns: 1 }).message, '本对话还可继续 1 轮。')
})

test('完成三十轮后阻止继续发送并引导新对话', () => {
  assert.deepEqual(turnLimitPresentation({ successful_turns: 30, remaining_turns: 0 }), {
    blocked: true,
    message: '本对话已完成 30 轮，请开启新对话继续。'
  })
})

test('服务端缺省和越界值会规范化', () => {
  assert.deepEqual(normalizeTurnLimit({ successful_turns: 32, remaining_turns: -2 }), {
    successful_turns: 30,
    remaining_turns: 0,
    max_turns: 30,
    reached: true
  })
})
