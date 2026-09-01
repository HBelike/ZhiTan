const WEB_CHANNEL = 'find-job-job-library-web-v1'
const EXTENSION_CHANNEL = 'find-job-job-library-extension-v1'
const DEFAULT_TIMEOUT_MS = 22000
let requestSequence = 0

const JOB_DETAIL_STRING_FIELDS = [
  'id',
  'securityId',
  'jobId',
  'bossId',
  'lid',
  'title',
  'salary',
  'experience',
  'degree',
  'city',
  'district',
  'recruiter',
  'recruiterAvatar',
  'company',
  'companyShort',
  'companyLogo',
  'industry',
  'scale',
  'stage',
  'sourceUrl'
]

function cloneString(value) {
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return ''
}

function cloneStringList(value) {
  if (!Array.isArray(value)) return []
  return Array.from(value, cloneString).filter(Boolean)
}

export function createJobDetailPayload(job = {}) {
  const source = job && typeof job === 'object' ? job : {}
  const fallback = Object.fromEntries(
    JOB_DETAIL_STRING_FIELDS.map((field) => [field, cloneString(source[field])])
  )
  fallback.skills = cloneStringList(source.skills)
  fallback.welfare = cloneStringList(source.welfare)
  fallback.recruiterOnline = Boolean(source.recruiterOnline)

  return {
    securityId: fallback.securityId,
    fallback
  }
}

export function createGreetingRequestPayload(job = {}, message = '') {
  return {
    securityId: cloneString(job?.securityId),
    jobId: cloneString(job?.jobId),
    bossId: cloneString(job?.bossId),
    lid: cloneString(job?.lid),
    message: cloneString(message)
  }
}

export class JobLibraryBridgeError extends Error {
  constructor(code, message, details = {}) {
    super(message)
    this.name = 'JobLibraryBridgeError'
    this.code = code
    this.retryable = details.retryable === true
    this.stopBatch = details.stopBatch === true
    this.retryMode = typeof details.retryMode === 'string' ? details.retryMode : ''
    this.submissionState = typeof details.submissionState === 'string' ? details.submissionState : ''
    this.defaultGreetingSent = details.defaultGreetingSent === true
  }
}

function requestExtension(action, payload = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const requestId = `job-library-${Date.now()}-${++requestSequence}`
    const timeoutId = window.setTimeout(() => {
      window.removeEventListener('message', handleMessage)
      reject(new JobLibraryBridgeError(
        'extension_unavailable',
        action === 'ping'
          ? '未检测到职位库浏览器助手，请先安装并启用扩展。'
          : action.includes('xiaohongshu')
            ? '浏览器助手响应超时，请确认小红书页面可以正常访问。'
            : '职位库浏览器助手响应超时，请确认 BOSS 页面可以正常访问。'
      ))
    }, timeoutMs)

    function handleMessage(event) {
      if (event.source !== window || event.origin !== window.location.origin) return
      const message = event.data
      if (!message || message.channel !== EXTENSION_CHANNEL || message.requestId !== requestId) return
      window.clearTimeout(timeoutId)
      window.removeEventListener('message', handleMessage)
      if (message.ok) {
        resolve(message.data)
        return
      }
      reject(new JobLibraryBridgeError(
        message.error?.code ?? 'extension_error',
        message.error?.message ?? '职位库助手调用失败。',
        message.error ?? {}
      ))
    }

    window.addEventListener('message', handleMessage)
    window.postMessage({ channel: WEB_CHANNEL, requestId, action, payload }, window.location.origin)
  })
}

export async function pingExtensionWithRetry(
  pingOnce,
  { attempts = 2, wait = () => new Promise((resolve) => window.setTimeout(resolve, 160)) } = {}
) {
  let lastError
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await pingOnce()
    } catch (error) {
      lastError = error
      if (!(error instanceof JobLibraryBridgeError)
        || error.code !== 'extension_unavailable'
        || attempt >= attempts - 1) {
        throw error
      }
      await wait()
    }
  }
  throw lastError
}

export const jobLibraryBridge = {
  ping() {
    return pingExtensionWithRetry(() => requestExtension('ping', {}, 1800))
  },
  listCities() {
    return requestExtension('list_cities')
  },
  searchJobs(query, page = 1, city = {}) {
    return requestExtension('search_jobs', {
      query,
      page,
      cityCode: String(city?.code ?? ''),
      cityName: String(city?.name ?? '')
    })
  },
  getJobDetail(job) {
    return requestExtension('get_job_detail', createJobDetailPayload(job))
  },
  searchXiaohongshuNotes(keyword, limit = 20) {
    return requestExtension('search_xiaohongshu_notes', {
      keyword: cloneString(keyword),
      limit: Math.max(5, Math.min(50, Number(limit) || 20))
    }, 90000)
  },
  getXiaohongshuNote(card) {
    return requestExtension('get_xiaohongshu_note', {
      noteId: cloneString(card?.noteId),
      signedUrl: cloneString(card?.signedUrl),
      title: cloneString(card?.title),
      authorName: cloneString(card?.authorName)
    }, 60000)
  },
  preflightGreeting(job, message) {
    return requestExtension('preflight_greeting', createGreetingRequestPayload(job, message))
  },
  sendGreeting(job, message) {
    return requestExtension('send_greeting', createGreetingRequestPayload(job, message), 60000)
  },
  retryGreetingMessage(job, message, context = {}) {
    return requestExtension('retry_greeting_message', {
      ...createGreetingRequestPayload(job, message),
      defaultGreetingSent: context.defaultGreetingSent === true
    }, 60000)
  }
}
