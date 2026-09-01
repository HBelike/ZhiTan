import assert from 'node:assert/strict'
import test from 'node:test'

import {
  nextResumeProcessingProgress,
  resumeUploadProgress
} from './career-resume-progress.js'

test('简历上传进度只占整体解析进度的前 32%', () => {
  assert.equal(resumeUploadProgress(0, 100), 4)
  assert.equal(resumeUploadProgress(50, 100), 16)
  assert.equal(resumeUploadProgress(100, 100), 32)
  assert.equal(resumeUploadProgress(10, 0), 4)
})

test('服务端解析进度逐渐放缓并停在 92% 等待真实结果', () => {
  assert.equal(nextResumeProcessingProgress(32), 36)
  assert.equal(nextResumeProcessingProgress(66), 68)
  assert.equal(nextResumeProcessingProgress(84), 85)
  assert.equal(nextResumeProcessingProgress(92), 92)
  assert.equal(nextResumeProcessingProgress(99), 92)
})
