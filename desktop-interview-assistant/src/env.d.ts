/// <reference types="vite/client" />

export {}

declare global {
  interface Window {
    interviewBridge?: {
      platform: string
      openLogin(apiBaseUrl: string): Promise<void>
      apiRequest<T>(apiBaseUrl: string, path: string, init?: { method?: string; body?: unknown }): Promise<T>
      openSocket(apiBaseUrl: string, sessionId: string): Promise<void>
      sendSocket(payload: unknown): void
      closeSocket(): void
      onSocketEvent(listener: (payload: unknown) => void): () => void
      onSocketClosed(listener: (payload: { code: number; reason: string }) => void): () => void
    }
  }
}
