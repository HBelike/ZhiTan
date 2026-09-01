const PREPARING_STEPS = new Set([
  'validate_input',
  'build_context',
  'parse_material',
  'extract_job_description',
  'redact_sensitive_data'
])

const ANALYZING_STEPS = new Set([
  'analyze_match',
  'generate_response'
])

const FINISHING_STEPS = new Set([
  'persist_history',
  'cleanup_temporary_files'
])

const TECHNICAL_ERROR_PATTERN = /(worker|turn|服务端|队列|lease|slot|heartbeat|[a-z]+(?:_[a-z0-9]+)+)/i

export function presentTurnProgress(event) {
  const step = String(event?.step ?? event?.key ?? '')
  const state = event?.state || 'running'
  const completed = state === 'completed'

  if (PREPARING_STEPS.has(step)) {
    return {
      key: 'preparing',
      label: completed ? '问题和资料已整理' : '正在整理问题和资料',
      state
    }
  }
  if (ANALYZING_STEPS.has(step)) {
    return {
      key: 'analyzing',
      label: completed ? '回复内容已整理' : '正在分析并组织回复',
      state
    }
  }
  if (FINISHING_STEPS.has(step)) {
    return {
      key: 'finishing',
      label: completed ? '本轮回复即将完成' : '正在完成本轮回复',
      state
    }
  }
  return {
    key: 'processing',
    label: completed ? '请求信息已处理' : '正在处理你的请求',
    state
  }
}

export function turnStatusText(turn) {
  return {
    queued: '消息已收到，正在等待回复',
    running: '求职助手正在回复，离开或刷新页面也不会中断',
    succeeded: '本轮回复已完成',
    failed: '本轮回复未完成，请查看对话中的说明',
    cancelled: '本轮回复已取消'
  }[turn?.status] ?? '正在处理你的请求'
}

export function submissionFeedback(ahead) {
  const count = Math.max(0, Number(ahead) || 0)
  return count > 0
    ? `消息已收到，前方还有 ${count} 条待回复消息。`
    : '消息已收到，求职助手将尽快回复。'
}

export function observedTurnStatus(turn) {
  if (turn?.status !== 'queued') return '正在分析并组织回复…'
  const ahead = Math.max(0, Number(turn?.conversation_position) || 0)
  return ahead > 0
    ? `正在等待回复，前方还有 ${ahead} 条消息`
    : '正在等待回复…'
}

export function publicTurnError(message) {
  const text = String(message ?? '').trim()
  if (!text || TECHNICAL_ERROR_PATTERN.test(text)) {
    return '本轮回复未完成，请稍后重试。'
  }
  return text
}
