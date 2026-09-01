import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createGreetingRequestPayload,
  createJobDetailPayload,
  JobLibraryBridgeError,
  pingExtensionWithRetry
} from './job-library-bridge.js'

test('浏览器桥公开小红书关键词与详情动作', async () => {
  const source = await import('node:fs/promises').then(({ readFile }) => readFile(new URL('job-library-bridge.js', import.meta.url), 'utf8'))
  assert.match(source, /search_xiaohongshu_notes/)
  assert.match(source, /get_xiaohongshu_note/)
  assert.match(source, /signedUrl/)
})

test('createJobDetailPayload converts a reactive-style job into clone-safe data', () => {
  const source = new Proxy({
    id: 'job-1',
    securityId: 'security-1',
    jobId: 'encrypt-job-1',
    bossId: 'encrypt-boss-1',
    lid: 'lid-1',
    title: '全栈开发工程师',
    salary: '15-30K·14薪',
    skills: new Proxy(['全栈开发', '后端开发经验'], {}),
    welfare: new Proxy(['五险一金'], {}),
    recruiterOnline: true,
    ignoredFunction() {}
  }, {})

  assert.throws(() => structuredClone(source), /could not be cloned/)

  const payload = createJobDetailPayload(source)

  assert.deepEqual(structuredClone(payload), {
    securityId: 'security-1',
    fallback: {
      id: 'job-1',
      securityId: 'security-1',
      jobId: 'encrypt-job-1',
      bossId: 'encrypt-boss-1',
      lid: 'lid-1',
      title: '全栈开发工程师',
      salary: '15-30K·14薪',
      experience: '',
      degree: '',
      city: '',
      district: '',
      skills: ['全栈开发', '后端开发经验'],
      welfare: ['五险一金'],
      recruiter: '',
      recruiterOnline: true,
      recruiterAvatar: '',
      company: '',
      companyShort: '',
      companyLogo: '',
      industry: '',
      scale: '',
      stage: '',
      sourceUrl: ''
    }
  })
})

test('createGreetingRequestPayload 只发送单个岗位标识和最终文案', () => {
  const payload = createGreetingRequestPayload({
    securityId: 'security-1',
    jobId: 'job-1',
    bossId: 'boss-1',
    lid: 'lid-1',
    description: '完整 JD 不应发送',
    resume: '简历正文不应发送'
  }, '您好，期待沟通。')

  assert.deepEqual(payload, {
    securityId: 'security-1',
    jobId: 'job-1',
    bossId: 'boss-1',
    lid: 'lid-1',
    message: '您好，期待沟通。'
  })
})

test('发送错误保留安全重试阶段与默认招呼状态', () => {
  const error = new JobLibraryBridgeError('verification_required', '请完成验证', {
    retryable: true,
    retryMode: 'message',
    submissionState: 'not_submitted',
    defaultGreetingSent: true
  })

  assert.equal(error.retryable, true)
  assert.equal(error.retryMode, 'message')
  assert.equal(error.submissionState, 'not_submitted')
  assert.equal(error.defaultGreetingSent, true)
})

test('扩展首次握手超时后自动重试一次', async () => {
  let attempts = 0
  const result = await pingExtensionWithRetry(async () => {
    attempts += 1
    if (attempts === 1) {
      throw new JobLibraryBridgeError('extension_unavailable', '后台仍在启动')
    }
    return { connected: true, version: '0.2.3' }
  }, { wait: async () => {} })

  assert.equal(attempts, 2)
  assert.equal(result.connected, true)
})

test('非连接类错误不重复请求扩展', async () => {
  let attempts = 0
  await assert.rejects(() => pingExtensionWithRetry(async () => {
    attempts += 1
    throw new JobLibraryBridgeError('forbidden_sender', '来源不允许')
  }, { wait: async () => {} }), /来源不允许/)
  assert.equal(attempts, 1)
})
