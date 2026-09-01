import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'

import {
  ASSESSMENT_CAPTURE_CAPABILITY,
  detectAssessmentPlatform,
  extractAssessmentFromPage,
  extractFunctionSignature,
  normalizeAssessmentCapture
} from '../assessment-capture.js'


test('assessment capture caps text and normalizes generic platform', () => {
  const capture = normalizeAssessmentCapture({
    sourcePlatform: 'unknown-site',
    visibleText: `  ${'x'.repeat(26000)}  `,
    problemCandidates: [' A  \n B ', ' A  \n B '],
    viewport: { width: 1440.4, height: 899.8, devicePixelRatio: 1.25 }
  })

  assert.equal(capture.visibleText.length, 25000)
  assert.equal(capture.sourcePlatform, 'generic')
  assert.deepEqual(capture.problemCandidates, ['A\nB'])
  assert.equal(capture.viewport.width, 1440)
  assert.equal(ASSESSMENT_CAPTURE_CAPABILITY, 'online_assessment_capture_v1')
})


test('assessment capture keeps only complete public tests', () => {
  const capture = normalizeAssessmentCapture({
    publicTestCandidates: [
      { input: '1 2', output: '3', explanation: '基础样例' },
      { input: '4 5', output: '' }
    ]
  })

  assert.equal(capture.publicTestCandidates.length, 1)
  assert.equal(capture.publicTestCandidates[0].output, '3')
})


test('assessment capture recognizes LeetCode China root domain', () => {
  assert.equal(detectAssessmentPlatform('leetcode.cn'), 'leetcode')
  assert.equal(detectAssessmentPlatform('www.leetcode.cn'), 'leetcode')
  assert.equal(detectAssessmentPlatform('leetcode.com'), 'leetcode')
  assert.equal(detectAssessmentPlatform('example.com'), 'generic')
})


test('assessment capture extracts LeetCode class method signature from starter code', () => {
  assert.equal(
    extractFunctionSignature('class Solution:\n    def groupAnagrams(self, strs: list[str]) -> list[list[str]]:\n        pass'),
    'def groupAnagrams(self, strs: list[str])'
  )
})


test('assessment page capture remains self-contained after Chrome serializes it', () => {
  const capture = runInNewContext(`(${extractAssessmentFromPage.toString()})()`, {
    document: {
      title: '49. 字母异位词分组 - 力扣',
      querySelectorAll: () => [],
      body: { querySelectorAll: () => [] }
    },
    location: {
      href: 'https://leetcode.cn/problems/group-anagrams/',
      hostname: 'leetcode.cn'
    },
    innerWidth: 1440,
    innerHeight: 900,
    devicePixelRatio: 1
  })

  assert.equal(capture.sourcePlatform, 'leetcode')
  assert.equal(capture.sourceTitle, '49. 字母异位词分组 - 力扣')
})


test('service worker has no third-party write or submit assessment action', () => {
  const worker = readFileSync(new URL('../service-worker.js', import.meta.url), 'utf8')
  const manifest = JSON.parse(readFileSync(new URL('../manifest.json', import.meta.url), 'utf8'))

  assert.match(worker, /claim_assessment_launch/)
  assert.match(worker, /refresh_assessment_problem/)
  assert.match(worker, /chrome\.runtime\.onStartup\.addListener/)
  assert.match(worker, /execution\?\.error/)
  assert.doesNotMatch(worker, /console\.error\(['"]线上笔试题面识别失败/)
  assert.doesNotMatch(worker, /submit_assessment|fill_assessment|insert_assessment/)
  assert.ok(manifest.permissions.includes('activeTab'))
  assert.ok(manifest.permissions.includes('storage'))
  assert.ok(!manifest.host_permissions.includes('<all_urls>'))
})
