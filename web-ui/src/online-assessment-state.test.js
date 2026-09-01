import assert from 'node:assert/strict'
import test from 'node:test'

import { confirmAssessmentProblem, createAssessmentState } from './online-assessment/state.js'

test('online assessment state ignores stale run results', () => {
  const manager = createAssessmentState()
  const first = manager.begin('analyzing')
  const second = manager.begin('generating')

  assert.equal(manager.accept(first, { phase: 'ready' }), false)
  assert.equal(manager.accept(second, { phase: 'testing' }), true)
  assert.equal(manager.state.phase, 'testing')
})

test('online assessment session excludes raw capture by construction', () => {
  const values = new Map()
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key)
  }
  const manager = createAssessmentState(storage)
  manager.accept(0, { problem: { title: '题目' }, solution: { code: 'print(1)' } })

  const persisted = [...values.values()][0]
  assert.match(persisted, /print\(1\)/)
  assert.doesNotMatch(persisted, /screenshot|visibleText|pageText/)
})

test('manual problem confirmation is persisted before a new run starts', () => {
  const manager = createAssessmentState()
  const confirmed = confirmAssessmentProblem({
    title: '字母异位词分组',
    confidence: 0.45,
    incomplete_reasons: ['无法判断题目输入输出接口']
  })

  manager.accept(0, { problem: confirmed })
  manager.begin('generating')

  assert.equal(manager.state.problem.confidence, 1)
  assert.deepEqual(manager.state.problem.incomplete_reasons, [])
})
