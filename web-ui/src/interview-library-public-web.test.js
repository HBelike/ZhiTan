import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const component = readFileSync(
  new URL('./components/InterviewLibraryPage.vue', import.meta.url),
  'utf8'
)
const helper = readFileSync(new URL('./public-web-collection.js', import.meta.url), 'utf8')

test('公开信息收集走服务端公开网页接口，不依赖浏览器扩展', () => {
  assert.match(component, /public-web-imports/)
  assert.match(component, /全网公开信息收集/)
  assert.match(component, /normalizePublicWebCollectionLimit/)
  const keywordForm = component.match(/<form v-else-if="collectionMode === 'keyword'"[^]*?<\/form>/)?.[0] ?? ''
  assert.doesNotMatch(keywordForm, /jobLibraryBridge|xiaohongshu-browser-imports|安装或更新浏览器助手/)
})

test('公开网页任务显示五阶段、八类计数和来源记录', () => {
  for (const stage of ['搜索公开网页', '地址去重', '提取正文与 OCR', 'Agent 甄别', '自动写入面经库']) {
    assert.match(helper, new RegExp(stage))
  }
  for (const label of ['发现地址', '已知地址', '已提取正文', '重复正文', '有效面经', '新入库', '已过滤', '失败']) {
    assert.match(component, new RegExp(label))
  }
  assert.match(component, /来源记录/)
  assert.match(component, /同正文来源/)
})
