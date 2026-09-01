import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const jobSearchVue = readFileSync(new URL('./components/JobSearchWorkspace.vue', import.meta.url), 'utf8')

test('岗位检索紧凑视图保留选中岗位并单独切换列表详情', () => {
  assert.match(jobSearchVue, /const compactView = ref\('list'\)/)
  assert.match(jobSearchVue, /function returnToJobList\(\)\s*\{\s*compactView\.value = 'list'/)
  assert.match(jobSearchVue, /'show-compact-detail': compactView === 'detail'/)
  assert.match(jobSearchVue, />返回岗位列表</)
  assert.doesNotMatch(
    jobSearchVue.match(/function returnToJobList\(\)[\s\S]*?\n\}/)?.[0] ?? '',
    /selectedJob(?:Id)?\.value\s*=/
  )
})

test('岗位检索使用组件容器查询并限制横向溢出', () => {
  assert.match(jobSearchVue, /container:\s*job-search\s*\/\s*inline-size/)
  assert.match(jobSearchVue, /@container job-search \(max-width:1000px\)/)
  assert.match(jobSearchVue, /@container job-search \(max-width:760px\)/)
  assert.match(jobSearchVue, /\.job-browser\.show-compact-detail \.job-results\s*\{\s*display:none/)
  assert.match(jobSearchVue, /\.detail-back-button\s*\{[^}]*min-height:40px/s)
})
