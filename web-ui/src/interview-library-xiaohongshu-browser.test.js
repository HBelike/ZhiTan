import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const component = readFileSync(
  new URL('./components/InterviewLibraryPage.vue', import.meta.url),
  'utf8'
)

test('保留小红书 URL 能力，但顶部信息收集入口改为公开网页', () => {
  assert.match(component, /小红书URL读取<\/button>[^]*公开信息收集<\/button>/)
  assert.match(component, /jobLibraryBridge\.searchXiaohongshuNotes/)
  assert.match(component, /jobLibraryBridge\.getXiaohongshuNote/)
  assert.match(component, /xiaohongshu-discoveries/)
  assert.match(component, /xiaohongshu-notes/)
  assert.match(component, /\/pause/)
  assert.match(component, /\/complete/)
})

test('历史小红书浏览器编排仍保留五到五十条的服务契约', () => {
  assert.match(component, /normalizeXiaohongshuCollectionLimit\(job\.requested_limit/)
  assert.match(component, /XIAOHONGSHU_COLLECTION_JOB_STORAGE_KEY/)
  assert.match(component, /runXiaohongshuBrowserCollection/)
  assert.match(component, /pauseXiaohongshuBrowserCollection/)
})

test('页面发给后端的请求不包含小红书详情签名参数', () => {
  assert.doesNotMatch(component, /xsec_token/)
  assert.match(component, /createXiaohongshuDiscoveryPayload/)
  assert.match(component, /createXiaohongshuNotePayload/)
})
