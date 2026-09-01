import test from 'node:test'
import assert from 'node:assert/strict'
import {
  generateGreetingBatch,
  GreetingRequestError,
  requestGreeting
} from './career-greeting-client.js'

function fullJob(id) {
  return {
    id,
    title: `Java 工程师 ${id}`,
    company: `示例公司 ${id}`,
    recruiter: '彭女士·招聘专员',
    description: '负责 Java 微服务开发、故障排查与版本交付。',
    skills: ['Java', '微服务'],
    sourceUrl: `https://www.zhipin.com/${id}`
  }
}

function successPayload(id) {
  return {
    job_key: id,
    message: `岗位 ${id} 的真实招呼语`,
    resume_evidence: [{ id: 'CV-001', summary: 'Java 微服务交付经历' }],
    jd_highlights: [{ id: 'JD-001', summary: '岗位关注微服务开发' }],
    warnings: [],
    model: { provider_key: 'deepseek', model_id: 'deepseek-v4-pro' }
  }
}

function jsonResponse(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    async json() { return payload }
  }
}

test('请求只发送候选人 id、完整岗位和上一版文案', async () => {
  const calls = []
  const result = await requestGreeting({
    candidateProfileId: 'candidate-1',
    job: fullJob('1'),
    previousMessage: '上一版'
  }, async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) })
    return jsonResponse(successPayload('1'))
  })

  assert.equal(result.job_key, '1')
  assert.equal(calls[0].url, '/api/career/greetings/generate')
  assert.equal(calls[0].body.candidate_profile_id, 'candidate-1')
  assert.equal(calls[0].body.previous_message, '上一版')
  assert.equal(calls[0].body.job.description, fullJob('1').description)
  assert.equal(calls[0].body.job.source_url, fullJob('1').sourceUrl)
  assert.equal('resume_outline' in calls[0].body, false)
})

test('接口错误保留稳定 code 和用户可读信息', async () => {
  await assert.rejects(
    requestGreeting({
      candidateProfileId: 'candidate-1',
      job: fullJob('1')
    }, async () => jsonResponse({
      detail: {
        code: 'greeting_model_unavailable',
        message: 'DeepSeek V4 Pro 尚未配置'
      }
    }, { ok: false, status: 409 })),
    (error) => {
      assert.equal(error instanceof GreetingRequestError, true)
      assert.equal(error.code, 'greeting_model_unavailable')
      assert.equal(error.message, 'DeepSeek V4 Pro 尚未配置')
      assert.equal(error.status, 409)
      return true
    }
  )
})

test('批次生成最多同时运行三个岗位且单条失败不阻断', async () => {
  let active = 0
  let peak = 0
  const settled = []

  await generateGreetingBatch(
    Array.from({ length: 7 }, (_, index) => fullJob(String(index + 1))),
    async (job) => {
      active += 1
      peak = Math.max(peak, active)
      await new Promise((resolve) => setTimeout(resolve, 8))
      active -= 1
      if (job.id === '4') throw new Error('模型失败')
      return successPayload(job.id)
    },
    3,
    (result) => settled.push(result)
  )

  assert.equal(peak, 3)
  assert.equal(settled.length, 7)
  assert.equal(settled.filter((item) => item.status === 'rejected').length, 1)
  assert.equal(settled.filter((item) => item.status === 'fulfilled').length, 6)
})

test('空批次不会创建 worker', async () => {
  let called = false
  await generateGreetingBatch([], async () => { called = true })
  assert.equal(called, false)
})
