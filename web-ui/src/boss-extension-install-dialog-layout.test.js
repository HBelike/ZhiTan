import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentUrl = new URL('./components/BossExtensionInstallDialog.vue', import.meta.url)

test('扩展安装弹窗移除右侧登录态说明', async () => {
  const source = await readFile(componentUrl, 'utf8')

  assert.doesNotMatch(source, /助手只在本机使用 BOSS 登录态/)
})

test('完整图文教程使用醒目的浏览器入口', async () => {
  const source = await readFile(componentUrl, 'utf8')

  assert.match(source, /安装遇到问题？/)
  assert.match(source, /class="guide-link chrome-guide"/)
  assert.match(source, /class="guide-link edge-guide"/)
  assert.match(source, /\.gate-guide-callout\{/)
})
