import { greetingJobKey } from './career-greeting-preview.js'

export class GreetingRequestError extends Error {
  constructor(code, message, status = 0) {
    super(message)
    this.name = 'GreetingRequestError'
    this.code = code || 'greeting_request_failed'
    this.status = Number(status) || 0
  }
}

function normalizedJob(job) {
  return {
    id: greetingJobKey(job),
    title: String(job?.title || '').trim(),
    company: String(job?.company || job?.companyShort || '').trim(),
    recruiter: String(job?.recruiter || '').trim(),
    description: String(job?.description || '').trim(),
    skills: Array.isArray(job?.skills)
      ? job.skills.map((item) => String(item).trim()).filter(Boolean).slice(0, 50)
      : [],
    source_url: String(job?.sourceUrl || job?.source_url || '').trim()
  }
}

async function responsePayload(response) {
  try {
    return await response.json()
  } catch {
    return {}
  }
}

export async function requestGreeting({
  candidateProfileId,
  job,
  previousMessage = ''
}, requestImpl = fetch) {
  const response = await requestImpl('/api/career/greetings/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      candidate_profile_id: String(candidateProfileId || ''),
      job: normalizedJob(job),
      previous_message: String(previousMessage || '')
    })
  })
  const payload = await responsePayload(response)
  if (!response.ok) {
    const detail = payload?.detail
    const code = typeof detail === 'object' ? detail?.code : ''
    const message = typeof detail === 'object'
      ? detail?.message
      : (typeof detail === 'string' ? detail : '')
    throw new GreetingRequestError(
      code || 'greeting_request_failed',
      message || `招呼语生成失败（HTTP ${response.status}）`,
      response.status
    )
  }
  return payload
}

export async function generateGreetingBatch(
  jobs,
  worker,
  concurrency = 3,
  onSettled = () => {}
) {
  const queue = Array.isArray(jobs) ? [...jobs] : []
  const workerCount = Math.min(
    queue.length,
    Math.max(1, Math.floor(Number(concurrency) || 1))
  )
  const run = async () => {
    while (queue.length) {
      const job = queue.shift()
      try {
        const value = await worker(job)
        onSettled({ status: 'fulfilled', job, value })
      } catch (reason) {
        onSettled({ status: 'rejected', job, reason })
      }
    }
  }
  await Promise.all(Array.from({ length: workerCount }, () => run()))
}
