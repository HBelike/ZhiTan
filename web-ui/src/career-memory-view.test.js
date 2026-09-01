import test from 'node:test'
import assert from 'node:assert/strict'

import { CAREER_MEMORY_TYPES, groupCareerMemories, memorySourceLabel } from './career-memory-view.js'

test('求职记忆按六类和状态分组', () => {
  const grouped = groupCareerMemories([
    { id: '1', memory_type: 'job_intention', status: 'active' },
    { id: '2', memory_type: 'award', status: 'candidate' }
  ])
  assert.equal(grouped.job_intention.active.length, 1)
  assert.equal(grouped.award.candidate.length, 1)
  assert.deepEqual(Object.keys(grouped), CAREER_MEMORY_TYPES)
})

test('来源标签不暴露消息正文', () => {
  assert.equal(memorySourceLabel({ source_kind: 'confirmed_resume', candidate_profile_version: 3 }), '确认简历 v3')
  assert.equal(memorySourceLabel({ source_kind: 'explicit_user_correction' }), '用户修正')
})
