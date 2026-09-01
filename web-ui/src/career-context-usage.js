export function normalizeContextUsage(input = {}) {
  const used = Math.max(0, Math.min(100, Math.round(Number(input.used_percent) || 0)))
  const remaining = Math.max(0, Math.min(100, Math.round(
    Number.isFinite(Number(input.remaining_percent)) ? Number(input.remaining_percent) : 100 - used
  )))
  const allowedStates = new Set(['normal', 'warning', 'compacting', 'degraded', 'hard_blocked'])
  return {
    used_percent: used,
    remaining_percent: remaining,
    state: allowedStates.has(input.state) ? input.state : 'normal',
    approximate: input.approximate !== false
  }
}

export function contextUsagePresentation(input = {}) {
  const usage = normalizeContextUsage(input)
  if (usage.state === 'hard_blocked') {
    return { tone: 'blocked', label: `上下文余量约 ${usage.remaining_percent}%`, spinning: false }
  }
  if (usage.state === 'compacting') {
    return { tone: 'compacting', label: '正在整理上下文', spinning: true }
  }
  if (usage.state === 'warning' || usage.state === 'degraded') {
    return { tone: 'warning', label: `上下文余量约 ${usage.remaining_percent}%`, spinning: false }
  }
  return { tone: 'normal', label: `上下文余量约 ${usage.remaining_percent}%`, spinning: false }
}
