import { describe, expect, it } from 'vitest'
import { InterviewSocketClient } from './client'

class FakeTransport {
  eventListener: ((payload: unknown) => void) | null = null
  closeListener: ((payload: { code: number; reason: string }) => void) | null = null
  openAction: (() => void) | null = null

  async openSocket() {
    queueMicrotask(() => this.openAction?.())
  }

  sendSocket() {}
  closeSocket() {}
  onSocketEvent(listener: (payload: unknown) => void) {
    this.eventListener = listener
    return () => { this.eventListener = null }
  }
  onSocketClosed(listener: (payload: { code: number; reason: string }) => void) {
    this.closeListener = listener
    return () => { this.closeListener = null }
  }
}

describe('实时连接准备', () => {
  it('收到 session.ready 后才允许开始采集', async () => {
    const transport = new FakeTransport()
    transport.openAction = () => transport.eventListener?.({ type: 'session.ready' })
    const events: string[] = []
    const client = new InterviewSocketClient(
      (event) => events.push(event.type),
      () => undefined,
      transport,
      100,
    )

    await expect(client.open('http://127.0.0.1:18080', 'session-id')).resolves.toBeUndefined()
    expect(events).toEqual(['session.ready'])
  })

  it('服务端初始化失败时返回界面错误而不是继续发送音频', async () => {
    const transport = new FakeTransport()
    transport.openAction = () => transport.eventListener?.({
      type: 'error',
      message: '未配置实时转写模型',
    })
    const client = new InterviewSocketClient(
      () => undefined,
      () => undefined,
      transport,
      100,
    )

    await expect(client.open('http://127.0.0.1:18080', 'session-id')).rejects.toThrow(
      '未配置实时转写模型',
    )
  })
})
