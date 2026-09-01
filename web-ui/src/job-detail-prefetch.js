export function createJobDetailLoader({ fetchDetail, keyOf }) {
  if (typeof fetchDetail !== 'function' || typeof keyOf !== 'function') {
    throw new TypeError('详情加载器需要 fetchDetail 和 keyOf')
  }

  const resolved = new Map()
  const pending = new Map()
  let generation = 0

  function prime(job) {
    if (!job?.description) return null
    const key = String(keyOf(job) || '')
    if (!key) return job
    const detail = { ...(resolved.get(key) ?? {}), ...job }
    resolved.set(key, detail)
    return detail
  }

  function peek(job) {
    const embedded = prime(job)
    if (embedded) return embedded
    const key = String(keyOf(job) || '')
    return key ? resolved.get(key) ?? null : null
  }

  function load(job) {
    const cached = peek(job)
    if (cached) return Promise.resolve(cached)
    const key = String(keyOf(job) || '')
    if (!key) return Promise.reject(new Error('岗位缺少稳定标识，无法读取详情'))
    if (pending.has(key)) return pending.get(key)

    const requestGeneration = generation
    let request
    try {
      request = Promise.resolve(fetchDetail(job))
        .then((detail) => {
          if (!detail?.description) throw new Error('岗位详情没有返回完整 JD')
          const merged = { ...job, ...detail }
          if (requestGeneration === generation) resolved.set(key, merged)
          return merged
        })
        .finally(() => {
          if (pending.get(key) === request) pending.delete(key)
        })
    } catch (error) {
      request = Promise.reject(error)
    }
    pending.set(key, request)
    return request
  }

  function clear() {
    generation += 1
    resolved.clear()
    pending.clear()
  }

  return { clear, load, peek, prime }
}

export async function prefetchJobDetails(jobs, loader, {
  concurrency = 2,
  onResolved = () => {},
  onRejected = () => {},
  shouldStop = () => false
} = {}) {
  const queue = Array.isArray(jobs) ? jobs : []
  const workerCount = Math.max(1, Math.min(Number(concurrency) || 1, queue.length || 1))
  let cursor = 0
  let stopped = false

  async function worker() {
    while (!stopped) {
      const index = cursor
      cursor += 1
      if (index >= queue.length) return
      const job = queue[index]
      try {
        const detail = await loader.load(job)
        await onResolved(detail, job)
      } catch (error) {
        await onRejected(error, job)
        if (shouldStop(error, job)) stopped = true
      }
    }
  }

  await Promise.all(Array.from({ length: workerCount }, () => worker()))
}
