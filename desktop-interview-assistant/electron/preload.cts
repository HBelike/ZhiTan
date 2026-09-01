const { contextBridge, ipcRenderer } = require('electron') as typeof import('electron')

contextBridge.exposeInMainWorld('interviewBridge', {
  platform: process.platform,
  openLogin: (apiBaseUrl: string) => ipcRenderer.invoke('auth:open', apiBaseUrl),
  apiRequest: <T,>(apiBaseUrl: string, path: string, init?: { method?: string; body?: unknown }) =>
    ipcRenderer.invoke('api:request', apiBaseUrl, path, init) as Promise<T>,
  openSocket: (apiBaseUrl: string, sessionId: string) =>
    ipcRenderer.invoke('socket:open', apiBaseUrl, sessionId),
  sendSocket: (payload: unknown) => ipcRenderer.send('socket:send', payload),
  closeSocket: () => ipcRenderer.send('socket:close'),
  onSocketEvent: (listener: (payload: unknown) => void) => {
    const wrapped = (_event: unknown, payload: unknown) => listener(payload)
    ipcRenderer.on('socket:event', wrapped)
    return () => ipcRenderer.removeListener('socket:event', wrapped)
  },
  onSocketClosed: (listener: (payload: { code: number; reason: string }) => void) => {
    const wrapped = (_event: unknown, payload: { code: number; reason: string }) => listener(payload)
    ipcRenderer.on('socket:closed', wrapped)
    return () => ipcRenderer.removeListener('socket:closed', wrapped)
  },
})
