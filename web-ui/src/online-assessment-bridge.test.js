import assert from 'node:assert/strict'
import test from 'node:test'

import { captureToApiPayload, mergeAssessmentCaptures } from './online-assessment/bridge.js'

test('online assessment bridge maps extension fields to backend contract', () => {
  const payload = captureToApiPayload({
    sourceUrl: 'https://example.test/problem',
    sourcePlatform: 'generic',
    visibleText: '题面',
    screenshotDataUrl: 'data:image/png;base64,AA',
    viewport: { width: 1440, height: 900, devicePixelRatio: 1.25 }
  })

  assert.equal(payload.source_url, 'https://example.test/problem')
  assert.equal(payload.screenshot_data_url, 'data:image/png;base64,AA')
  assert.equal(payload.viewport.device_pixel_ratio, 1.25)
})

test('append capture merges paragraphs without duplicating them', () => {
  const merged = mergeAssessmentCaptures(
    { visibleText: '第一段\n第二段', problemCandidates: ['题面 A'] },
    { visibleText: '第二段\n第三段', problemCandidates: ['题面 A', '题面 B'] }
  )

  assert.equal(merged.visibleText, '第一段\n第二段\n第三段')
  assert.deepEqual(merged.problemCandidates, ['题面 A', '题面 B'])
})
