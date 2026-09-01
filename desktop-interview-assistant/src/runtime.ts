import { startCapture, type CaptureController } from './audio/capture'
import { ChannelSequencer } from './audio/pcm'
import { InterviewSocketClient } from './protocol/client'
import { applyServerEvent, sessionState } from './stores/session'

let socket: InterviewSocketClient | null = null
let capture: CaptureController | null = null

function toBase64(frame: Int16Array): string {
  const bytes = new Uint8Array(frame.buffer, frame.byteOffset, frame.byteLength)
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000))
  }
  return btoa(binary)
}

export async function beginLiveSession(apiBaseUrl: string, sessionId: string, microphoneDeviceId?: string): Promise<void> {
  sessionState.apiBaseUrl = apiBaseUrl
  sessionState.sessionId = sessionId
  sessionState.connection = 'connecting'
  const sequencer = new ChannelSequencer()
  socket = new InterviewSocketClient(
    (event) => applyServerEvent(sessionState, event),
    (reason) => {
      sessionState.connection = 'closed'
      sessionState.error = reason
      void stopCaptureOnly()
    },
  )
  await socket.open(apiBaseUrl, sessionId)
  try {
    capture = await startCapture({
      microphoneDeviceId,
      onFrame(role, frame) {
        socket?.send({ type: 'audio.append', channel: role, sequence: sequencer.next(role), pcm_base64: toBase64(frame) })
      },
      onLevel(role, level) {
        if (role === 'interviewer') sessionState.interviewerLevel = level
        else sessionState.candidateLevel = level
      },
    })
    socket.send({ type: 'session.start' })
  } catch (error) {
    socket.close()
    socket = null
    throw error
  }
}

export function sendLiveEvent(payload: unknown): void {
  socket?.send(payload)
}

export async function stopCaptureOnly(): Promise<void> {
  await capture?.stop()
  capture = null
  sessionState.interviewerLevel = 0
  sessionState.candidateLevel = 0
}

export async function endLiveSession(): Promise<void> {
  socket?.send({ type: 'session.end' })
  await stopCaptureOnly()
  socket?.close()
  socket = null
  sessionState.connection = 'closed'
}
