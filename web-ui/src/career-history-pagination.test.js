import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_HISTORY_PAGE_SIZE,
  HISTORY_PAGE_SIZE_OPTIONS,
  historyPageRange,
  historyPageSizeForViewportHeight,
  normalizeHistoryPage,
  normalizeHistoryPageTarget,
  pageRequestUrl,
  resolveHistoryPageSize
} from './career-history-pagination.js'

test('会话列表默认使用标准视口的七条容量', () => {
  assert.equal(DEFAULT_HISTORY_PAGE_SIZE, 7)
  assert.equal(pageRequestUrl(1, DEFAULT_HISTORY_PAGE_SIZE), '/api/career/conversations?page=1&page_size=7')
})

test('会话列表根据视口高度选择五、七、十条容量', () => {
  assert.equal(historyPageSizeForViewportHeight(720), 5)
  assert.equal(historyPageSizeForViewportHeight(900), 5)
  assert.equal(historyPageSizeForViewportHeight(1080), 7)
  assert.equal(historyPageSizeForViewportHeight(1440), 10)
  assert.equal(historyPageSizeForViewportHeight('invalid'), 7)
})

test('用户分页偏好仅接受分页器提供的容量并优先于视口默认值', () => {
  assert.deepEqual(HISTORY_PAGE_SIZE_OPTIONS, [5, 7, 10, 15])
  assert.equal(resolveHistoryPageSize('15', 720), 15)
  assert.equal(resolveHistoryPageSize('9', 1440), 10)
  assert.equal(resolveHistoryPageSize('', 900), 5)
})

test('分页响应修正非法页码并计算区间', () => {
  const page = normalizeHistoryPage({ items: [], page: 8, page_size: 10, total: 32, total_pages: 4 })
  assert.equal(page.page, 4)
  assert.deepEqual(historyPageRange({ page: 3, pageSize: 10, total: 32 }), { start: 21, end: 30 })
})

test('空列表区间保持为零', () => {
  assert.deepEqual(historyPageRange({ page: 1, pageSize: 10, total: 0 }), { start: 0, end: 0 })
})

test('指定页跳转会修正空值和越界页码', () => {
  assert.equal(normalizeHistoryPageTarget('', 4), 1)
  assert.equal(normalizeHistoryPageTarget(3, 4), 3)
  assert.equal(normalizeHistoryPageTarget(99, 4), 4)
  assert.equal(normalizeHistoryPageTarget(-2, 4), 1)
})
