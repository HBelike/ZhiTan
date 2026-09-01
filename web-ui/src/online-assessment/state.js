export const ASSESSMENT_SESSION_KEY = 'career-online-assessment-session-v1'

export const ASSESSMENT_PHASES = Object.freeze([
  'waiting_for_capture',
  'capturing',
  'analyzing',
  'generating',
  'testing',
  'repairing',
  'ready',
  'partial',
  'failed'
])

export function confirmAssessmentProblem(problem) {
  if (!problem || typeof problem !== 'object') return null
  return { ...problem, confidence: 1, incomplete_reasons: [] }
}

export function createAssessmentState(storage = null) {
  const state = {
    runVersion: 0,
    phase: 'waiting_for_capture',
    problem: null,
    solution: null,
    tests: [],
    report: null,
    items: [],
    captureToken: '',
    sourceTabId: null
  }
  const persist = () => {
    if (!storage) return
    const safe = {
      ...state,
      captureToken: state.captureToken,
      sourceTabId: state.sourceTabId
    }
    storage.setItem(ASSESSMENT_SESSION_KEY, JSON.stringify(safe))
  }
  return {
    state,
    restore() {
      if (!storage) return state
      try {
        const value = JSON.parse(storage.getItem(ASSESSMENT_SESSION_KEY) || 'null')
        if (value && typeof value === 'object') Object.assign(state, value)
      } catch {
        storage.removeItem(ASSESSMENT_SESSION_KEY)
      }
      return state
    },
    begin(phase = 'capturing') {
      state.runVersion += 1
      state.phase = phase
      state.tests = []
      state.report = null
      persist()
      return state.runVersion
    },
    accept(runVersion, patch) {
      if (runVersion !== state.runVersion) return false
      Object.assign(state, patch)
      persist()
      return true
    },
    persist,
    clear() {
      Object.assign(state, {
        runVersion: state.runVersion + 1,
        phase: 'waiting_for_capture',
        problem: null,
        solution: null,
        tests: [],
        report: null,
        items: [],
        captureToken: '',
        sourceTabId: null
      })
      storage?.removeItem(ASSESSMENT_SESSION_KEY)
    }
  }
}
