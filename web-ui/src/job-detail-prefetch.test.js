import test from 'node:test'
import assert from 'node:assert/strict'
import {
  createJobDetailLoader,
  prefetchJobDetails
} from './job-detail-prefetch.js'

const keyOf = (job) => String(job.id)

test('同一岗位的预取、勾选和预览共用一个详情请求', async () => {
  let resolveDetail
  let calls = 0
  const loader = createJobDetailLoader({
    keyOf,
    fetchDetail: async (job) => {
      calls += 1
      return new Promise((resolve) => {
        resolveDetail = () => resolve({ ...job, description: '完整 JD' })
      })
    }
  })

  const first = loader.load({ id: 'job-1', title: '工程师' })
  const second = loader.load({ id: 'job-1', title: '工程师' })
  assert.equal(first, second)
  assert.equal(calls, 1)

  resolveDetail()
  assert.equal((await first).description, '完整 JD')
  assert.equal(loader.peek({ id: 'job-1' }).description, '完整 JD')
  assert.equal((await loader.load({ id: 'job-1' })).description, '完整 JD')
  assert.equal(calls, 1)
})

test('搜索结果已经带完整 JD 时不会再次请求详情', async () => {
  let calls = 0
  const loader = createJobDetailLoader({
    keyOf,
    fetchDetail: async () => {
      calls += 1
      return null
    }
  })
  const job = { id: 'job-1', description: '已有完整 JD' }

  assert.equal((await loader.load(job)).description, '已有完整 JD')
  assert.equal(calls, 0)
})

test('详情失败后清除进行中记录并允许用户重试', async () => {
  let calls = 0
  const loader = createJobDetailLoader({
    keyOf,
    fetchDetail: async (job) => {
      calls += 1
      if (calls === 1) throw new Error('temporary')
      return { ...job, description: '重试成功' }
    }
  })

  await assert.rejects(loader.load({ id: 'job-1' }), /temporary/)
  assert.equal((await loader.load({ id: 'job-1' })).description, '重试成功')
  assert.equal(calls, 2)
})

test('后台预取服从并发上限且单条失败不影响其他岗位', async () => {
  let active = 0
  let peak = 0
  const resolved = []
  const rejected = []
  const loader = createJobDetailLoader({
    keyOf,
    fetchDetail: async (job) => {
      active += 1
      peak = Math.max(peak, active)
      await new Promise((resolve) => setTimeout(resolve, 5))
      active -= 1
      if (job.id === 'job-2') throw new Error('failed')
      return { ...job, description: `JD ${job.id}` }
    }
  })

  await prefetchJobDetails(
    [{ id: 'job-1' }, { id: 'job-2' }, { id: 'job-3' }, { id: 'job-4' }],
    loader,
    {
      concurrency: 2,
      onResolved: (detail) => resolved.push(detail.id),
      onRejected: (error, job) => rejected.push([job.id, error.message])
    }
  )

  assert.equal(peak, 2)
  assert.deepEqual(resolved.sort(), ['job-1', 'job-3', 'job-4'])
  assert.deepEqual(rejected, [['job-2', 'failed']])
})

test('遇到限流等停止条件后不再领取新的后台预取任务', async () => {
  const started = []
  const loader = createJobDetailLoader({
    keyOf,
    fetchDetail: async (job) => {
      started.push(job.id)
      const error = new Error('rate limited')
      error.code = 'rate_limited'
      throw error
    }
  })

  await prefetchJobDetails(
    [{ id: 'job-1' }, { id: 'job-2' }, { id: 'job-3' }],
    loader,
    {
      concurrency: 1,
      shouldStop: (error) => error.code === 'rate_limited'
    }
  )

  assert.deepEqual(started, ['job-1'])
})
