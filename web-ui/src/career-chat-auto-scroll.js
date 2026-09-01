function defaultRequestFrame(callback) {
  if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
    return window.requestAnimationFrame(callback)
  }
  return setTimeout(callback, 0)
}

function defaultCancelFrame(frameId) {
  if (typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function') {
    window.cancelAnimationFrame(frameId)
    return
  }
  clearTimeout(frameId)
}

export function createChatAutoScroller({
  getContainer = () => null,
  getTarget = () => null,
  requestFrame = defaultRequestFrame,
  cancelFrame = defaultCancelFrame
} = {}) {
  let scheduledFrame = null

  function request() {
    if (scheduledFrame !== null) return
    scheduledFrame = requestFrame(() => {
      scheduledFrame = null
      const target = getTarget()
      if (target && typeof target.scrollIntoView === 'function') {
        target.scrollIntoView({ behavior: 'auto', block: 'end', inline: 'nearest' })
        return
      }
      const container = getContainer()
      if (container) container.scrollTop = container.scrollHeight
    })
  }

  function cancel() {
    if (scheduledFrame === null) return
    cancelFrame(scheduledFrame)
    scheduledFrame = null
  }

  return { request, cancel }
}
