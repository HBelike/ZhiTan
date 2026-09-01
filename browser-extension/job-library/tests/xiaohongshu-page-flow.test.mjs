import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'

const pageSource = await readFile(new URL('../xiaohongshu-page.js', import.meta.url), 'utf8')
const workerSource = await readFile(new URL('../service-worker.js', import.meta.url), 'utf8')
const bridgeSource = await readFile(new URL('../content-script.js', import.meta.url), 'utf8')

test('详情读取从搜索页真实卡片进入并恢复搜索上下文', () => {
  assert.match(pageSource, /openXiaohongshuNoteFromSearchPage/)
  assert.match(pageSource, /anchor\.click\(\)/)
  assert.match(pageSource, /restoreXiaohongshuSearchPage/)
  assert.match(workerSource, /openXiaohongshuNoteFromSearchPage/)
  assert.match(workerSource, /restoreXiaohongshuSearchPage/)
  assert.doesNotMatch(workerSource, /navigateXiaohongshuTab\(tab\.id, parsed\.toString\(\)\)/)
})

test('扩展更新后会幂等地向已打开业务页补注入通信脚本', () => {
  assert.match(bridgeSource, /__findJobBrowserBridge/)
  assert.match(bridgeSource, /removeEventListener/)
  assert.match(workerSource, /chrome\.runtime\.onInstalled\.addListener/)
  assert.match(workerSource, /files:\s*\['content-script\.js'\]/)
})
