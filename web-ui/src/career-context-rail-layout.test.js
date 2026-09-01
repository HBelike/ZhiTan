import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentUrl = new URL('./components/CareerAssistantPage.vue', import.meta.url)

test('职位信息栏拖拽手柄不被外层容器裁切', async () => {
  const source = await readFile(componentUrl, 'utf8')

  assert.match(source, /\.context-rail-slot\{[^}]*overflow:visible[^}]*\}/)
  assert.doesNotMatch(source, /\.context-rail-slot\{[^}]*overflow:hidden[^}]*\}/)
  assert.match(source, /\.context-rail-resizer\{[^}]*left:-13px[^}]*cursor:col-resize[^}]*\}/)
})
