import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import {
  beginSelectionIntent,
  cancelSelectionIntent,
  createSelectionIntentState,
  isCurrentSelectionIntent,
  resetSelectionIntents
} from './job-search-multi-selection.js'

test('取消中的详情请求不能在返回后重新加入岗位', () => {
  const state = createSelectionIntentState()
  const token = beginSelectionIntent(state, 'job-1')
  cancelSelectionIntent(state, 'job-1')
  assert.equal(isCurrentSelectionIntent(state, 'job-1', token), false)
})

test('后发选择意图会让同岗位旧请求失效', () => {
  const state = createSelectionIntentState()
  const first = beginSelectionIntent(state, 'job-1')
  const second = beginSelectionIntent(state, 'job-1')
  assert.equal(isCurrentSelectionIntent(state, 'job-1', first), false)
  assert.equal(isCurrentSelectionIntent(state, 'job-1', second), true)
})

test('重置搜索会统一作废所有仍在加载的岗位', () => {
  const state = createSelectionIntentState()
  const token = beginSelectionIntent(state, 'job-1')
  resetSelectionIntents(state)
  assert.equal(isCurrentSelectionIntent(state, 'job-1', token), false)
})

test('多选岗位卡包含独立复选框且不再依赖右侧加入按钮', async () => {
  const source = await readFile(
    new URL('./components/JobSearchWorkspace.vue', import.meta.url),
    'utf8'
  )
  assert.match(source, /class="job-selection-checkbox"/)
  assert.match(source, /@click\.stop="toggleJobFromCard\(job\)"/)
  assert.match(source, /role="checkbox"/)
  assert.doesNotMatch(source, /加入本批|移出本批/)
})

test('勾选立即更新批次并在后台补齐详情', async () => {
  const source = await readFile(
    new URL('./components/JobSearchWorkspace.vue', import.meta.url),
    'utf8'
  )
  const toggleStart = source.indexOf('async function toggleJobFromCard')
  const toggleEnd = source.indexOf('\nfunction resetJobResults', toggleStart)
  const toggleSource = source.slice(toggleStart, toggleEnd)
  assert.ok(toggleSource.indexOf('toggleGreetingJob(') < toggleSource.indexOf('void hydrateSelectedJob(job, token)'))
  assert.match(toggleSource, /void hydrateSelectedJob\(job, token\)/)
  assert.match(source, /async function hydrateSelectedJob[\s\S]*?detailLoader\.load\(job\)/)
  assert.match(source, /prefetchCurrentJobs\(jobs\.value, sequence\)/)
  assert.match(source, /detailLoader\.load\(job\)/)
  assert.doesNotMatch(source, /\.job-detail\.refreshing[^\n]*opacity/)
})
