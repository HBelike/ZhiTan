async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
    ...options
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail || `请求失败（HTTP ${response.status}）`)
  return payload
}

export async function streamNdjson(path, body, onEvent, signal) {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail || `生成失败（HTTP ${response.status}）`)
  }
  if (!response.body) throw new Error('浏览器不支持流式读取答案')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line))
    }
    if (done) break
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer))
}

export const assessmentApi = {
  analyze(runVersion, capture, signal) {
    return requestJson('/api/career/online-assessments/analyze', {
      method: 'POST',
      body: JSON.stringify({ run_version: runVersion, capture }),
      signal
    })
  },
  solve(runVersion, problem, onEvent, signal) {
    return streamNdjson(
      '/api/career/online-assessments/solve/stream',
      { run_version: runVersion, problem },
      onEvent,
      signal
    )
  },
  execute(runVersion, problem, solution, tests = [], autoRepair = true, signal) {
    return requestJson('/api/career/online-assessments/execute', {
      method: 'POST',
      body: JSON.stringify({
        run_version: runVersion,
        problem,
        solution,
        tests,
        auto_repair: autoRepair
      }),
      signal
    })
  },
  archivePreview(payload) {
    return requestJson('/api/career/online-assessments/archive/preview', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
  },
  archive(payload) {
    return requestJson('/api/career/online-assessments/archive', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
  }
}
