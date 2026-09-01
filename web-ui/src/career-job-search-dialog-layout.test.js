import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentUrl = new URL('./components/CareerJobSearchDialog.vue', import.meta.url)

test('职位检索弹窗移除重复标题并保留可访问名称', async () => {
  const source = await readFile(componentUrl, 'utf8')

  assert.doesNotMatch(source, /选择当前会话的目标岗位/)
  assert.match(source, /aria-label="职位检索"/)
})

test('桌面职位检索弹窗占视口宽高的百分之八十并保持居中', async () => {
  const source = await readFile(componentUrl, 'utf8')

  assert.match(source, /width:80vw/)
  assert.match(source, /height:80dvh/)
  assert.match(source, /place-items:center/)
})
