export type ServerEvent = Record<string, unknown> & { type: string }

import { bridge } from '../bridge'

interface SocketTransport {
  openSocket(apiBaseUrl: string, sessionId: string): Promise<void>
  sendSocket(payload: unknown): void
  closeSocket(): void
  onSocketEvent(listener: (payload: unknown) => void): () => void
  onSocketClosed(listener: (payload: { code: number; reason: string }) => void): () => void
}

export class InterviewSocketClient {
  private removeEventListener: (() => void) | null = null
  private removeCloseListener: (() => void) | null = null

  constructor(
    private readonly onEvent: (event: ServerEvent) => void,
    private readonly onClosed: (reason: string) => void,
    private readonly transport: SocketTransport = bridge,
    private readonly readyTimeoutMs = 15_000,
  ) {}

  async open(apiBaseUrl: string, sessionId: string): Promise<void> {
    let resolveReady: (() => void) | null = null
    let rejectReady: ((reason: Error) => void) | null = null
    let ready = false
    const readyPromise = new Promise<void>((resolve, reject) => {
      resolveReady = resolve
      rejectReady = reject
    })
    const timeout = setTimeout(() => rejectReady?.(new Error('实时转写服务准备超时')), this.readyTimeoutMs)

    this.removeEventListener = this.transport.onSocketEvent((payload) => {
      if (!payload || typeof payload !== 'object' || !('type' in payload)) return
      const event = payload as ServerEvent
      this.onEvent(event)
      if (event.type === 'session.ready') {
        ready = true
        resolveReady?.()
      } else if (!ready && event.type === 'error') {
        rejectReady?.(new Error(String(event.message ?? '实时转写服务启动失败')))
      }
    })
    this.removeCloseListener = this.transport.onSocketClosed((payload) => {
      const reason = payload.reason || `连接已关闭（${payload.code}）`
      this.onClosed(reason)
      if (!ready) rejectReady?.(new Error(reason))
    })
    try {
      await this.transport.openSocket(apiBaseUrl, sessionId)
      await readyPromise
    } catch (error) {
      this.close()
      throw error
    } finally {
      clearTimeout(timeout)
    }
  }

  send(payload: unknown): void {
    this.transport.sendSocket(payload)
  }

  close(): void {
    this.transport.closeSocket()
    this.removeEventListener?.()
    this.removeCloseListener?.()
    this.removeEventListener = null
    this.removeCloseListener = null
  }
}
