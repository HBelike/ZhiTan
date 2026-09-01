import { reactive } from 'vue'

export interface Utterance {
  role: 'interviewer' | 'candidate'
  text: string
  sequence: number
}

export interface SessionState {
  sessionId: string
  apiBaseUrl: string
  connection: 'idle' | 'connecting' | 'ready' | 'closed' | 'error'
  asrStatus: string
  answerStatus: string
  error: string
  utterances: Utterance[]
  partials: Record<'interviewer' | 'candidate', string>
  activeQuestionVersion: number
  activeAttempt: number
  question: string
  intent: string
  answerText: string
  interviewerLevel: number
  candidateLevel: number
}

export function createSessionState(): SessionState {
  return {
    sessionId: '', apiBaseUrl: 'http://127.0.0.1:8000', connection: 'idle', asrStatus: '等待开始',
    answerStatus: '等待问题', error: '', utterances: [], partials: { interviewer: '', candidate: '' },
    activeQuestionVersion: 0, activeAttempt: 0, question: '', intent: '', answerText: '',
    interviewerLevel: 0, candidateLevel: 0,
  }
}

export const sessionState = reactive(createSessionState())

export function resetSessionState(): void {
  Object.assign(sessionState, createSessionState(), { apiBaseUrl: sessionState.apiBaseUrl })
}

export function applyServerEvent(state: SessionState, event: Record<string, unknown>): void {
  const type = String(event.type ?? '')
  if (type === 'session.ready') {
    state.connection = 'ready'
    state.asrStatus = '正在转写'
  } else if (type === 'transcript.partial') {
    const role = event.role === 'candidate' ? 'candidate' : 'interviewer'
    state.partials[role] = String(event.text ?? '')
  } else if (type === 'transcript.final') {
    const role = event.role === 'candidate' ? 'candidate' : 'interviewer'
    state.partials[role] = ''
    state.utterances.push({ role, text: String(event.text ?? ''), sequence: Number(event.sequence ?? 0) })
  } else if (type === 'question.detected') {
    state.activeQuestionVersion = Number(event.question_version ?? 0)
    state.activeAttempt = 0
    state.question = String(event.question ?? '')
    state.intent = String(event.intent ?? '')
    state.answerText = ''
    state.answerStatus = '已识别问题'
  } else if (type.startsWith('answer.')) {
    const version = Number(event.question_version ?? 0)
    if (version < state.activeQuestionVersion) return
    if (type === 'answer.started') {
      state.activeAttempt = Number(event.attempt ?? 1)
      state.answerText = ''
      state.answerStatus = '正在生成'
    } else if (type === 'answer.delta' && Number(event.attempt ?? 0) === state.activeAttempt) {
      state.answerText += String(event.delta ?? '')
    } else if (type === 'answer.completed') state.answerStatus = '回答完成'
    else if (type === 'answer.cancelled') state.answerStatus = '已停止'
  } else if (type === 'error') {
    state.error = String(event.message ?? '实时服务发生错误')
    state.answerStatus = '需要处理'
  }
}
