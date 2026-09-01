import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentUrl = new URL('./components/CareerAssistantPage.vue', import.meta.url)

test('会话详情展示不等待活动 Turn 恢复完成', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const selectionStart = source.indexOf('async function selectConversation')
  const selectionEnd = source.indexOf('function conversationTitleFromText', selectionStart)
  const selectionSource = source.slice(selectionStart, selectionEnd)

  assert.match(selectionSource, /void restoreServerQueueSafely\(conversationId\)/)
  assert.doesNotMatch(selectionSource, /await restoreServerQueue\(conversationId\)/)
  assert.match(source, /function syncSendingState\(\)[\s\S]*isActiveTurn\(lastTurn\.value\)/)
})

test('快速切换会取消并延迟旧的上下文用量请求', async () => {
  const source = await readFile(componentUrl, 'utf8')

  assert.match(source, /const CONTEXT_USAGE_DEBOUNCE_MS = 250/)
  assert.match(source, /contextUsageAbortController\?\.abort\(\)/)
  assert.match(source, /signal: request\.controller\.signal/)
  assert.match(source, /setTimeout\(\(\) => \{[\s\S]*loadContextUsage\(request\)/)
})
