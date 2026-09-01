function isActiveTurnStatus(status) {
  return status === 'queued' || status === 'running'
}

export function createTurnObservationCoordinator({
  createController = () => new AbortController()
} = {}) {
  let current = null

  function cancel() {
    if (!current) return false
    current.controller.abort()
    current = null
    return true
  }

  return {
    begin(conversationId, turnId) {
      if (!conversationId || !turnId) throw new Error('会话和 Turn 标识不能为空。')
      cancel()
      const controller = createController()
      const observation = {
        conversationId,
        turnId,
        controller,
        signal: controller.signal
      }
      current = observation
      return observation
    },
    cancel,
    isCurrent(observation) {
      return Boolean(observation && current === observation && !observation.signal.aborted)
    },
    finish(observation) {
      if (!observation || current !== observation) return false
      current = null
      return true
    }
  }
}

export function shouldReconcileConversationTurn(latestTurn, pendingTurns = []) {
  return isActiveTurnStatus(latestTurn?.status)
    && (!Array.isArray(pendingTurns) || pendingTurns.length === 0)
}
