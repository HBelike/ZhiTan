const MAX_TRANSCRIPTS = 100
const MAX_HISTORY = 50

export function createInterviewState() {
  return {
    connectionState: 'idle',
    activeChannels: [],
    transcripts: [],
    partialTranscripts: {},
    currentQuestion: '',
    questionIntent: '',
    questionVersion: 0,
    answerAttempt: 0,
    answerText: '',
    answerStatus: 'idle',
    lastDeltaIndex: 0,
    history: [],
    error: null
  }
}

function matchesCurrentAnswer(state, event) {
  return Number(event.question_version) === state.questionVersion
    && Number(event.attempt) === state.answerAttempt
}

export function reduceInterviewEvent(state, event) {
  const next = { ...state, error: event.type === 'error' ? event : state.error }
  if (event.type === 'session.ready') {
    return {
      ...next,
      connectionState: 'ready',
      activeChannels: Array.isArray(event.active_channels) ? [...event.active_channels] : []
    }
  }
  if (event.type === 'transcript.partial') {
    return {
      ...next,
      partialTranscripts: { ...next.partialTranscripts, [event.channel]: event.text || '' }
    }
  }
  if (event.type === 'transcript.final') {
    const transcript = {
      channel: event.channel,
      role: event.role,
      sequence: event.sequence,
      text: event.text,
      receivedAt: Date.now()
    }
    return {
      ...next,
      transcripts: [...next.transcripts, transcript].slice(-MAX_TRANSCRIPTS),
      partialTranscripts: { ...next.partialTranscripts, [event.channel]: '' }
    }
  }
  if (event.type === 'question.detected') {
    return {
      ...next,
      currentQuestion: event.question || '',
      questionIntent: event.intent || '',
      questionVersion: Number(event.question_version) || 0,
      answerAttempt: 0,
      answerText: '',
      answerStatus: 'waiting',
      lastDeltaIndex: 0,
      error: null
    }
  }
  if (event.type === 'answer.started') {
    if (Number(event.question_version) !== next.questionVersion) return state
    return {
      ...next,
      answerAttempt: Number(event.attempt) || 1,
      answerText: '',
      answerStatus: 'generating',
      lastDeltaIndex: 0,
      error: null
    }
  }
  if (event.type === 'answer.delta') {
    if (!matchesCurrentAnswer(next, event)) return state
    const deltaIndex = Number(event.delta_index) || next.lastDeltaIndex + 1
    if (deltaIndex <= next.lastDeltaIndex) return state
    return {
      ...next,
      answerText: next.answerText + String(event.delta || ''),
      lastDeltaIndex: deltaIndex
    }
  }
  if (event.type === 'answer.completed') {
    if (!matchesCurrentAnswer(next, event)) return state
    const completedText = typeof event.answer_text === 'string' ? event.answer_text : next.answerText
    const item = {
      questionVersion: next.questionVersion,
      attempt: next.answerAttempt,
      question: next.currentQuestion,
      intent: next.questionIntent,
      answer: completedText
    }
    const history = next.history.filter((entry) => (
      entry.questionVersion !== item.questionVersion || entry.attempt !== item.attempt
    ))
    return {
      ...next,
      answerText: completedText,
      answerStatus: 'completed',
      history: [...history, item].slice(-MAX_HISTORY)
    }
  }
  if (event.type === 'answer.cancelled') {
    return matchesCurrentAnswer(next, event) ? { ...next, answerStatus: 'cancelled' } : state
  }
  if (event.type === 'error') return { ...next, answerStatus: event.code === 'answer_failed' ? 'failed' : next.answerStatus }
  return state
}
