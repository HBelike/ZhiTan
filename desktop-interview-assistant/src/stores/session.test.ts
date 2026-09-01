import { describe, expect, it } from 'vitest'
import { applyServerEvent, createSessionState } from './session'

describe('实时会话状态', () => {
  it('忽略旧问题版本的迟到回答', () => {
    const state = createSessionState()
    applyServerEvent(state, { type: 'question.detected', question_version: 2, question: '新问题', intent: 'knowledge' })
    applyServerEvent(state, { type: 'answer.delta', question_version: 1, attempt: 1, delta: '迟到回答' })
    expect(state.answerText).toBe('')
  })

  it('按固定角色保存 final 转写', () => {
    const state = createSessionState()
    applyServerEvent(state, { type: 'transcript.final', role: 'interviewer', channel: 'interviewer', sequence: 1, text: '请介绍项目？' })
    applyServerEvent(state, { type: 'transcript.final', role: 'candidate', channel: 'candidate', sequence: 1, text: '好的' })
    expect(state.utterances.map((item) => item.role)).toEqual(['interviewer', 'candidate'])
  })
})
