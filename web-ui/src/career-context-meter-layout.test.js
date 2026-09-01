import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

test('上下文余量圆环位于发送按钮左侧并提供无障碍状态', async () => {
  const page = await readFile(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')
  const meter = await readFile(new URL('./components/CareerContextMeter.vue', import.meta.url), 'utf8')
  const modelIndex = page.indexOf('<select class="model-select"')
  const submitToolsIndex = page.indexOf('<div class="composer-submit-tools">')
  const meterIndex = page.indexOf('<CareerContextMeter')
  const sendIndex = page.indexOf('<button class="send-button"')
  assert.ok(modelIndex >= 0 && modelIndex < submitToolsIndex)
  assert.ok(submitToolsIndex >= 0 && submitToolsIndex < meterIndex)
  assert.ok(meterIndex >= 0 && meterIndex < sendIndex)
  assert.match(meter, /role="status"/)
  assert.match(meter, /stroke-dasharray/)
  assert.match(meter, /正在整理上下文|正在估算上下文/)
})

test('页面切换会话与模型时取消旧请求并延迟重新估算', async () => {
  const page = await readFile(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')
  assert.match(page, /contextUsageRequestId/)
  assert.match(page, /contextUsageRevision/)
  assert.match(
    page,
    /selectedConversation\.value\?\.id,\s*resolvedSelectedProfileId\.value,\s*contextUsageRevision\.value/,
  )
  assert.match(page, /scheduleContextUsageLoad/)
  assert.match(page, /contextUsageRequestId \+= 1/)
  assert.match(page, /contextUsageAbortController\?\.abort\(\)/)
  assert.match(page, /CONTEXT_USAGE_DEBOUNCE_MS/)
  assert.match(page, /contextUsageRevision\.value \+= 1/)
  assert.match(page, /loadContextUsage/)
  assert.match(page, /model_profile_id=/)
})
