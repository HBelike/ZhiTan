import { describe, expect, it } from 'vitest'
import { buildSessionRequest } from './setup'

describe('面试会话配置', () => {
  it('只提交转写与回答模型，不携带任何参考资料', () => {
    const payload = buildSessionRequest('asr-model', 'answer-model')

    expect(structuredClone(payload)).toEqual({
      asr_model_profile_id: 'asr-model',
      answer_model_profile_id: 'answer-model',
    })
    expect(payload).not.toHaveProperty('candidate_profile_id')
    expect(payload).not.toHaveProperty('target_role_profile_id')
    expect(payload).not.toHaveProperty('interview_experience_ids')
  })
})
