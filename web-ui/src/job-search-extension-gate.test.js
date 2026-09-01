import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentUrl = new URL('./components/JobSearchWorkspace.vue', import.meta.url)

test('岗位工作区初始化只静默检测扩展，不自动打开安装弹窗', async () => {
  const source = await readFile(componentUrl, 'utf8')

  assert.match(source, /checkBridge\(\{ loadCatalog = true, interactive = false \} = \{\}\)/)
  assert.match(source, /shouldOpenBossExtensionGate\(connection\.status, \{ interactive \}\)/)
  assert.match(source, /checkBridge\(\{ loadCatalog: false, interactive: true \}\)/)
})
