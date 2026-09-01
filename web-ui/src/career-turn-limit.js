export const MAX_CAREER_CONVERSATION_TURNS = 30

export function normalizeTurnLimit(input = {}) {
  const maximum = Math.max(1, Number(input.max_turns) || MAX_CAREER_CONVERSATION_TURNS)
  const successful = Math.max(0, Math.min(maximum, Number(input.successful_turns) || 0))
  const remaining = Math.max(0, Math.min(
    maximum,
    Number.isFinite(Number(input.remaining_turns)) ? Number(input.remaining_turns) : maximum - successful
  ))
  return {
    successful_turns: successful,
    remaining_turns: remaining,
    max_turns: maximum,
    reached: input.reached === true || remaining === 0
  }
}

export function turnLimitPresentation(input = {}) {
  const limit = normalizeTurnLimit(input)
  if (limit.reached) {
    return {
      blocked: true,
      message: `本对话已完成 ${limit.max_turns} 轮，请开启新对话继续。`
    }
  }
  if (limit.remaining_turns <= 2) {
    return {
      blocked: false,
      message: `本对话还可继续 ${limit.remaining_turns} 轮。`
    }
  }
  return { blocked: false, message: '' }
}
