import assert from 'node:assert/strict'
import test from 'node:test'

import {
  normalizePublicWebCollectionLimit,
  normalizePublicWebSummary,
  publicWebAvailability,
  publicWebProgress
} from './public-web-collection.js'

test('全网公开信息收集数量固定在 5 到 10，默认 10', () => {
  assert.equal(normalizePublicWebCollectionLimit(''), 10)
  assert.equal(normalizePublicWebCollectionLimit(2), 5)
  assert.equal(normalizePublicWebCollectionLimit(11), 10)
})

test('任务摘要稳定映射八类计数', () => {
  assert.deepEqual(normalizePublicWebSummary({
    discovered_count: 30,
    known_url_count: 5,
    scraped_count: 25,
    duplicate_count: 3,
    valid_count: 16,
    imported_count: 12,
    filtered_count: 7,
    failed_count: 2
  }), {
    discovered: 30,
    knownUrl: 5,
    scraped: 25,
    duplicate: 3,
    valid: 16,
    imported: 12,
    filtered: 7,
    failed: 2
  })
})

test('后端阶段映射为五阶段进度且完成态为百分百', () => {
  assert.deepEqual(publicWebProgress({ phase: 'ledger', progress_percent: 15 }, 'running'), {
    currentIndex: 1,
    percent: 15,
    detail: ''
  })
  assert.equal(publicWebProgress({ phase: 'completed' }, 'succeeded').currentIndex, 4)
  assert.equal(publicWebProgress({ phase: 'completed' }, 'succeeded').percent, 100)
})

test('Firecrawl 未配置时页面获得明确不可用原因', () => {
  assert.deepEqual(publicWebAvailability([{
    key: 'public_web',
    ready: false,
    unavailable_reason: '请配置 FIRECRAWL_API_KEY。'
  }]), { ready: false, reason: '请配置 FIRECRAWL_API_KEY。' })
})
