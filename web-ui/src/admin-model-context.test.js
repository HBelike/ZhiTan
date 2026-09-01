import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import { normalizeAppRoute } from './navigation-access.js'
import { modelProfilesFromAvailability } from './admin-model-context.js'

test('模型上下文页面从可用性响应中读取嵌套模型档案', () => {
  const profiles = modelProfilesFromAvailability({
    items: [
      {
        profile: {
          profile_key: 'deepseek-deepseek-v4-flash',
          display_name: 'DeepSeek 模型连接'
        },
        readiness: 'ready'
      },
      { readiness: 'unconfigured' }
    ]
  })

  assert.deepEqual(profiles, [
    {
      profile_key: 'deepseek-deepseek-v4-flash',
      display_name: 'DeepSeek 模型连接'
    }
  ])
})

test('模型上下文页面只出现在管理台并展示不可修改的95%硬限制', async () => {
  const admin = await readFile(new URL('./components/AdminConsolePage.vue', import.meta.url), 'utf8')
  const panel = await readFile(new URL('./components/AdminModelContextPanel.vue', import.meta.url), 'utf8')
  assert.match(admin, /\/admin\/model-context/)
  assert.match(panel, /上下文容量/)
  assert.match(panel, /压缩触发比例/)
  assert.match(panel, /压缩目标比例/)
  assert.match(panel, /95%/)
  assert.equal(normalizeAppRoute('/admin/model-context'), '/admin/model-context')
})

test('模型上下文页面移除重复说明并只用档案键作为卡片标题', async () => {
  const panel = await readFile(new URL('./components/AdminModelContextPanel.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(panel, /context-rule-note/)
  assert.match(panel, /<strong class="context-policy-title">\{\{ row\.profile_key \}\}<\/strong>/)
  assert.doesNotMatch(panel, /<strong>\{\{ row\.display_name \}\}<\/strong>/)
  assert.doesNotMatch(panel, /<code>\{\{ row\.profile_key \}\}<\/code>/)
})

test('策略面板在浏览器和服务端同时校验核心大小关系', async () => {
  const panel = await readFile(new URL('./components/AdminModelContextPanel.vue', import.meta.url), 'utf8')
  assert.match(panel, /outputTokens > windowTokens/)
  assert.match(panel, /模型最大输出/)
  assert.doesNotMatch(panel, /<span>预留输出<\/span>/)
  assert.match(panel, /系统 100000 Token 上限/)
  assert.match(panel, /target >= trigger/)
  assert.match(panel, /context_window_source: 'admin'/)
})
