import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

test('求职记忆页面和路由已经从产品入口移除', async () => {
  const app = await readFile(new URL('./App.vue', import.meta.url), 'utf8')
  const navigation = await readFile(new URL('./navigation-access.js', import.meta.url), 'utf8')
  assert.doesNotMatch(app, /CareerMemoryPage/)
  assert.doesNotMatch(app, /\/career\/memories/)
  assert.doesNotMatch(navigation, /\/career\/memories/)
})

test('会话页不再展示求职记忆入口、来源抽屉和记忆删除语义', async () => {
  const page = await readFile(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(page, /CareerMemoryUsageDrawer/)
  assert.doesNotMatch(page, /我的求职记忆/)
  assert.doesNotMatch(page, /查看本回答使用的求职记忆/)
  assert.doesNotMatch(page, /forget_derived_memories/)
  assert.doesNotMatch(page, /career_space_id/)
  assert.doesNotMatch(page, /长期求职记忆/)
  assert.match(page, /会话及其历史记录已永久删除/)
})
