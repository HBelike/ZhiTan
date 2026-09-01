import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const appUrl = new URL('./App.vue', import.meta.url)
const navigationUrl = new URL('./navigation-access.js', import.meta.url)

test('工作台实现和短视频蓝图仍保留但不再挂载公开路由', async () => {
  const source = await readFile(appUrl, 'utf8')
  const navigationSource = await readFile(navigationUrl, 'utf8')

  assert.match(source, /const isStoryboardReviewVisible = false/)
  assert.match(source, /\.\.\.\(isStoryboardReviewVisible[\s\S]*?\/review\/storyboard/)
  assert.match(source, /v-if="isStoryboardReviewVisible" class="storyboard-home"/)
  assert.match(source, /normalizeAppRoute\(pathname, routeItems\.map\(\(item\) => item\.path\)\)/)
  assert.doesNotMatch(navigationSource, /reviewRoutes\.includes\(pathname\)/)
  assert.doesNotMatch(navigationSource, /pathname\.startsWith\('\/review'\)/)
})
