export function createSelectionIntentState() {
  return { counter: 0, tokens: new Map() }
}

export function beginSelectionIntent(state, key) {
  const token = Number(state.counter || 0) + 1
  state.counter = token
  state.tokens.set(String(key), token)
  return token
}

export function cancelSelectionIntent(state, key) {
  state.tokens.delete(String(key))
}

export function isCurrentSelectionIntent(state, key, token) {
  return state.tokens.get(String(key)) === token
}

export function resetSelectionIntents(state) {
  state.counter = Number(state.counter || 0) + 1
  state.tokens.clear()
}
