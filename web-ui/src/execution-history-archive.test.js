import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const appVue = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const stylesCss = readFileSync(new URL('./styles.css', import.meta.url), 'utf8')
const themeCss = readFileSync(new URL('./theme.css', import.meta.url), 'utf8')

test('历史资源页提供同一 content_id 的原文入口', () => {
  assert.match(
    appVue,
    /class="archive-article-entry"[\s\S]*navigateTo\(`\/review\/article\?content_id=\$\{activeContentId\}`\)/
  )
  assert.match(appVue, /<h2>文章原文<\/h2>/)
  assert.match(appVue, /打开完整原文/)
})

test('历史文章路由按 content_id 读取详情并保持只读', () => {
  assert.match(
    appVue,
    /\['\/review\/assets', '\/review\/article'\]\.includes\(currentRoute\.value\)\s*\? contentIdFromLocation\(\)/
  )
  assert.match(appVue, /v-if="!contentIdFromLocation\(\)" class="review-action-row"/)
  assert.match(appVue, /contentIdFromLocation\(\) \? '← 返回本次归档'/)
})

test('每张历史卡片左下角显示生成时间', () => {
  assert.match(appVue, /class="history-item-time"\s*:datetime="item\.created_at"/)
  assert.match(appVue, /formatArchiveTimestamp\(item\.created_at\)/)
  assert.match(stylesCss, /\.history-item-bottom[\s\S]*justify-content:\s*space-between/)
})

test('蓝白主题覆盖历史卡片、时间戳和原文入口', () => {
  assert.match(themeCss, /html\[data-ui-theme="blue"\][^{]*\.archive-article-entry/)
  assert.match(themeCss, /html\[data-ui-theme="blue"\][^{]*\.history-content-id/)
  assert.match(themeCss, /html\[data-ui-theme="blue"\][^{]*\.history-item-time/)
  assert.match(themeCss, /html\[data-ui-theme="blue"\][^{]*\.history-item-footer em/)
})
