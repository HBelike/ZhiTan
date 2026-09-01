import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  BOSS_CHAT_PAGE_EVIDENCE,
  buildBossChatUrl,
  classifyBossChatPageState,
  classifyOutgoingMessageEvidence,
  classifyFriendAddResponse,
  createTabBusyGreetingFailure,
  hasNewLogicalOutgoingMessage,
  isTransientTabEditError,
  normalizeOutgoingMessageText,
  normalizeGreetingPayload,
  retryTransientTabEdit,
  updateLogicalMessageObservation,
  withGreetingRetryContext
} from '../boss-greeting.js'

const payload = {
  securityId: 'secure/1',
  jobId: 'job 1',
  bossId: 'boss+1',
  lid: 'lid?1',
  message: '您好，期待进一步沟通。'
}

const extensionRoot = new URL('../', import.meta.url)

test('真实发送 payload 只要求岗位标识和文案', () => {
  assert.deepEqual(normalizeGreetingPayload(payload), payload)
  assert.throws(() => normalizeGreetingPayload({ ...payload, message: '' }), /招呼语不能为空/)
  assert.throws(() => normalizeGreetingPayload({ ...payload, bossId: '' }), /招聘者标识/)
})

test('聊天 URL 只使用 BOSS 官方聊天页并编码岗位标识', () => {
  const url = new URL(buildBossChatUrl(payload))
  assert.equal(url.origin, 'https://www.zhipin.com')
  assert.equal(url.pathname, '/web/geek/chat')
  assert.equal(url.searchParams.get('id'), 'boss+1')
  assert.equal(url.searchParams.get('jobId'), 'job 1')
  assert.equal(url.searchParams.get('securityId'), 'secure/1')
  assert.equal(url.searchParams.get('lid'), 'lid?1')
})

test('默认招呼语发送后仍继续发送定制文案', () => {
  assert.deepEqual(classifyFriendAddResponse({ code: 0, zpData: { showGreeting: false } }), { ok: true })
  assert.deepEqual(classifyFriendAddResponse({ code: 0, zpData: { showGreeting: true } }), {
    ok: true,
    defaultGreetingSent: true
  })
})

test('建立沟通结果严格区分限流、验证和正常继续', () => {
  assert.equal(classifyFriendAddResponse({ code: 1, message: '操作过于频繁' }).code, 'rate_limited')
  assert.deepEqual(classifyFriendAddResponse({ code: 1, message: '请完成安全验证' }), {
    code: 'verification_required',
    message: '请完成安全验证',
    stopBatch: true,
    retryable: true,
    retryMode: 'full',
    submissionState: 'not_submitted'
  })
  assert.equal(classifyFriendAddResponse({ code: 1, message: '职位已下线' }).code, 'job_unavailable')
  assert.equal(classifyFriendAddResponse({ code: 1, message: '已经沟通过该招聘者' }).code, 'already_contacted')
  assert.equal(classifyFriendAddResponse({ code: 37 }).code, 'login_required')
})

test('聊天页安全验证只识别明确挑战，不匹配普通安全说明', () => {
  assert.equal(classifyBossChatPageState({
    url: 'https://www.zhipin.com/web/geek/chat',
    pageText: '请注意账号安全，平台可能要求安全验证。'
  }), null)
  assert.equal(classifyBossChatPageState({
    url: 'https://www.zhipin.com/web/geek/chat?securityId=verify-token',
    pageText: '正常聊天页面'
  }), null)
  assert.equal(classifyBossChatPageState({
    url: 'https://www.zhipin.com/web/user/security-check',
    pageText: ''
  }, BOSS_CHAT_PAGE_EVIDENCE).code, 'verification_required')
  assert.equal(classifyBossChatPageState({
    url: 'https://www.zhipin.com/web/geek/chat',
    pageText: '请完成安全验证后继续',
    submissionState: 'not_submitted'
  }).submissionState, 'not_submitted')
})

test('只有明确未提交或明确失败的定制文案允许重发', () => {
  assert.deepEqual(withGreetingRetryContext({
    code: 'verification_required',
    message: '请完成安全验证',
    stopBatch: true,
    submissionState: 'not_submitted'
  }, { defaultGreetingSent: true }), {
    code: 'verification_required',
    message: '请完成安全验证',
    stopBatch: true,
    submissionState: 'not_submitted',
    retryable: true,
    retryMode: 'message',
    defaultGreetingSent: true
  })
  assert.equal(withGreetingRetryContext({
    code: 'send_unknown',
    message: '结果未知',
    submissionState: 'unknown'
  }).retryable, false)
})

test('Chrome 标签页临时锁定会退避重试且不会吞掉其他错误', async () => {
  let attempts = 0
  const waits = []
  const result = await retryTransientTabEdit(async () => {
    attempts += 1
    if (attempts < 3) throw new Error('Tabs cannot be edited right now (user may be dragging a tab).')
    return 'ready'
  }, {
    wait: async (milliseconds) => waits.push(milliseconds)
  })

  assert.equal(result, 'ready')
  assert.equal(attempts, 3)
  assert.deepEqual(waits, [160, 320])
  assert.equal(isTransientTabEditError(new Error('Tabs cannot be edited right now (user may be dragging a tab).')), true)
  assert.equal(isTransientTabEditError(new Error('No tab with id: 1')), false)

  let nonTransientAttempts = 0
  await assert.rejects(() => retryTransientTabEdit(async () => {
    nonTransientAttempts += 1
    throw new Error('No tab with id: 1')
  }, { wait: async () => undefined }), /No tab with id/)
  assert.equal(nonTransientAttempts, 1)
})

test('聊天标签页持续锁定时保留默认招呼状态并允许仅重发定制文案', () => {
  assert.deepEqual(createTabBusyGreetingFailure({ defaultGreetingSent: true }), {
    code: 'browser_tab_busy',
    message: '浏览器正在调整标签页，请稍候后重新发送。',
    stopBatch: true,
    submissionState: 'not_submitted',
    retryable: true,
    retryMode: 'message',
    defaultGreetingSent: true
  })
})

test('网页桥与后台同时开放仅重发定制文案动作', async () => {
  const [contentScript, serviceWorker] = await Promise.all([
    readFile(new URL('content-script.js', extensionRoot), 'utf8'),
    readFile(new URL('service-worker.js', extensionRoot), 'utf8')
  ])
  assert.match(contentScript, /'retry_greeting_message'/)
  assert.match(serviceWorker, /action !== 'retry_greeting_message'/)
  assert.match(serviceWorker, /withGreetingRetryContext/)
  assert.match(serviceWorker, /retryTransientTabEdit\(\(\) => chrome\.tabs\.update/)
  assert.match(serviceWorker, /if \(isTransientTabEditError\(error\)\) throw error/)
  assert.match(serviceWorker, /createTabBusyGreetingFailure/)
  assert.match(serviceWorker, /'greeting_submission_state'/)
  assert.match(serviceWorker, /capabilities: GREETING_CAPABILITIES/)
  assert.match(serviceWorker, /sendButton\.click\(\)\s*return \{ ok: true, status: 'submitted' \}/)
  assert.doesNotMatch(serviceWorker, /new MutationObserver/)
  assert.match(serviceWorker, /data-message-direction="outgoing"/)
  assert.doesNotMatch(serviceWorker, /new WeakMap/)
})

test('聊天回执优先识别明确送达与明确失败状态', () => {
  assert.equal(classifyOutgoingMessageEvidence({ statusClasses: 'status status-delivery' }), 'sent')
  assert.equal(classifyOutgoingMessageEvidence({ statusText: '已读' }), 'sent')
  assert.equal(classifyOutgoingMessageEvidence({ statusClasses: 'status status-fail' }), 'failed')
  assert.equal(classifyOutgoingMessageEvidence({ statusText: '发送失败，请重试' }), 'failed')
})

test('BOSS 不显示旧送达 class 时以稳定的新本人消息确认提交', () => {
  assert.equal(classifyOutgoingMessageEvidence({
    isNew: true,
    inputCleared: true,
    stableForMs: 1999
  }), 'pending')
  assert.equal(classifyOutgoingMessageEvidence({
    isNew: true,
    inputCleared: true,
    stableForMs: 2000
  }), 'sent')
})

test('BOSS 消息文本只归一化空白和不可见字符', () => {
  assert.equal(
    normalizeOutgoingMessageText('  您好\u200b，期待\u00a0进一步\n沟通。  '),
    '您好，期待 进一步 沟通。'
  )
  assert.notEqual(
    normalizeOutgoingMessageText('您好，期待进一步沟通。'),
    normalizeOutgoingMessageText('您好！期待进一步沟通。')
  )
})

test('逻辑消息基线能识别新增与虚拟列表位移但拒绝历史同文案', () => {
  const expected = '您好，期待进一步沟通。'
  const baseline = ['历史消息', expected]

  assert.equal(hasNewLogicalOutgoingMessage(baseline, baseline, expected), false)
  assert.equal(hasNewLogicalOutgoingMessage(baseline, [...baseline, expected], expected), true)
  assert.equal(hasNewLogicalOutgoingMessage(
    ['更早消息', '上一条消息'],
    ['上一条消息', expected],
    expected
  ), true)
  assert.equal(hasNewLogicalOutgoingMessage(
    ['更早消息', expected],
    [expected],
    expected
  ), false)
})

test('逻辑消息稳定计时不受短暂 DOM 重绘影响且不会在消息消失时成功', () => {
  let observation = updateLogicalMessageObservation({}, {
    isNewLogicalMessage: true,
    inputCleared: true
  }, 1000)
  assert.equal(observation.stableForMs, 0)

  observation = updateLogicalMessageObservation(observation, {
    isNewLogicalMessage: false,
    inputCleared: true
  }, 1100)
  assert.equal(observation.isNew, false)
  assert.equal(observation.firstSeenAt, 1000)

  observation = updateLogicalMessageObservation(observation, {
    isNewLogicalMessage: true,
    inputCleared: true
  }, 1200)
  assert.equal(observation.stableForMs, 200)

  for (let now = 1300; now <= 3000; now += 100) {
    observation = updateLogicalMessageObservation(observation, {
      isNewLogicalMessage: true,
      inputCleared: true
    }, now)
  }
  assert.equal(classifyOutgoingMessageEvidence(observation), 'sent')

  const disappeared = updateLogicalMessageObservation(observation, {
    isNewLogicalMessage: false,
    inputCleared: true
  }, 3401)
  assert.equal(disappeared.firstSeenAt, 0)
  assert.equal(classifyOutgoingMessageEvidence(disappeared), 'pending')
})

test('历史同文案和未清空输入框不能伪造新发送成功', () => {
  assert.equal(classifyOutgoingMessageEvidence({
    isNew: false,
    inputCleared: true,
    stableForMs: 5000
  }), 'pending')
  assert.equal(classifyOutgoingMessageEvidence({
    isNew: true,
    inputCleared: false,
    stableForMs: 5000
  }), 'pending')
})
