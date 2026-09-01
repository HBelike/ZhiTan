import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const appUrl = new URL('./App.vue', import.meta.url)
const navigationUrl = new URL('./navigation-access.js', import.meta.url)

test('短视频蓝图保留实现但默认不进入审核界面', async () => {
  const source = await readFile(appUrl, 'utf8')
  const navigationSource = await readFile(navigationUrl, 'utf8')

  assert.match(source, /const isStoryboardReviewVisible = false/)
  assert.match(source, /\.\.\.\(isStoryboardReviewVisible[\s\S]*?\/review\/storyboard/)
  assert.match(source, /v-if="isStoryboardReviewVisible" class="storyboard-home"/)
  assert.match(source, /normalizeAppRoute\(pathname, routeItems\.map\(\(item\) => item\.path\)\)/)
  assert.match(navigationSource, /reviewRoutes\.includes\(pathname\) \? pathname : '\/review'/)
})
