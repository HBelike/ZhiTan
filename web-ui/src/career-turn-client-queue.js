export function restoreServerTurns(items = []) {
  return [...items]
    .filter((turn) => turn?.id && ['queued', 'running'].includes(turn.status))
    .sort((left, right) => Number(left.queue_sequence ?? 0) - Number(right.queue_sequence ?? 0))
    .map((turn) => ({
      id: turn.id,
      serverTurn: { ...turn },
      content: turn.content || '等待回复的消息',
      temporaryMessageId: turn.temporaryMessageId || '',
      streamedText: '',
      progress: []
    }))
}

export function upsertServerTurn(currentTurns, nextTurn) {
  const normalized = restoreServerTurns([nextTurn])[0]
  if (!normalized) return currentTurns.filter((item) => item.id !== nextTurn?.id)
  const existing = currentTurns.find((item) => item.id === normalized.id)
  const merged = existing
    ? {
        ...existing,
        serverTurn: { ...existing.serverTurn, ...normalized.serverTurn },
        content: nextTurn.content || existing.content,
        temporaryMessageId: nextTurn.temporaryMessageId || existing.temporaryMessageId
      }
    : normalized
  return [...currentTurns.filter((item) => item.id !== merged.id), merged]
    .sort((left, right) => Number(left.serverTurn.queue_sequence ?? 0) - Number(right.serverTurn.queue_sequence ?? 0))
}

export function removeServerTurn(currentTurns, turnId) {
  return currentTurns.filter((item) => item.id !== turnId)
}

export function firstServerTurn(currentTurns) {
  return currentTurns[0] ?? null
}
