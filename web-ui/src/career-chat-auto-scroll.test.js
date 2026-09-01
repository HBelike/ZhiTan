import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { createChatAutoScroller } from './career-chat-auto-scroll.js'

test('流式内容高频更新时每帧只滚动一次并始终跟随到底部', () => {
  const frames = []
  const container = { scrollHeight: 960, scrollTop: 120 }
  const autoScroller = createChatAutoScroller({
    getContainer: () => container,
    requestFrame: (callback) => {
      frames.push(callback)
      return frames.length
    },
    cancelFrame: () => {}
  })

  autoScroller.request()
  autoScroller.request()
  autoScroller.request()

  assert.equal(frames.length, 1)
  frames.shift()()
  assert.equal(container.scrollTop, 960)

  container.scrollHeight = 1280
  autoScroller.request()
  assert.equal(frames.length, 1)
  frames.shift()()
  assert.equal(container.scrollTop, 1280)
})

test('没有消息容器时安全跳过，取消后不执行遗留滚动', () => {
  const frames = new Map()
  const cancelled = []
  let nextFrameId = 0
  const autoScroller = createChatAutoScroller({
    getContainer: () => null,
    requestFrame: (callback) => {
      const frameId = ++nextFrameId
      frames.set(frameId, callback)
      return frameId
    },
    cancelFrame: (frameId) => {
      cancelled.push(frameId)
      frames.delete(frameId)
    }
  })

  autoScroller.request()
  autoScroller.cancel()

  assert.deepEqual(cancelled, [1])
  assert.equal(frames.size, 0)
})

test('优先把流式回复末尾锚点保持在视口中', () => {
  const frames = []
  const calls = []
  const autoScroller = createChatAutoScroller({
    getContainer: () => ({ scrollHeight: 1200, scrollTop: 0 }),
    getTarget: () => ({
      scrollIntoView: (options) => calls.push(options)
    }),
    requestFrame: (callback) => {
      frames.push(callback)
      return frames.length
    },
    cancelFrame: () => {}
  })

  autoScroller.request()
  frames.shift()()

  assert.deepEqual(calls, [{ behavior: 'auto', block: 'end', inline: 'nearest' }])
})

test('求职助手在发送、进度和流式正文变化后触发自动滚动', async () => {
  const source = await readFile(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')

  assert.match(source, /createChatAutoScroller/)
  assert.match(source, /ref="messageListRef"/)
  assert.match(source, /ref="latestMessageRef"/)
  assert.match(source, /\[sending, streamedAssistantText, streamStatus, streamProgress, feedback/)
  assert.match(source, /chatAutoScroller\.cancel\(\)/)
})
