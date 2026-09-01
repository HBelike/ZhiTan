import test from 'node:test'
import assert from 'node:assert/strict'

import { contextUsagePresentation, normalizeContextUsage } from './career-context-usage.js'

test('上下文圆环按使用率返回用户可理解状态', () => {
  assert.deepEqual(contextUsagePresentation({ used_percent: 59, remaining_percent: 41 }), {
    tone: 'normal', label: '上下文余量约 41%', spinning: false
  })
  assert.equal(contextUsagePresentation({ used_percent: 70, remaining_percent: 30, state: 'warning' }).tone, 'warning')
  assert.equal(contextUsagePresentation({ used_percent: 82, remaining_percent: 18, state: 'compacting' }).spinning, true)
  assert.equal(contextUsagePresentation({ used_percent: 95, remaining_percent: 5, state: 'hard_blocked' }).tone, 'blocked')
})

test('服务端近似值被限制在百分比范围内', () => {
  assert.deepEqual(normalizeContextUsage({ used_percent: 120, remaining_percent: -20, state: 'unknown' }), {
    used_percent: 100,
    remaining_percent: 0,
    state: 'normal',
    approximate: true
  })
})
