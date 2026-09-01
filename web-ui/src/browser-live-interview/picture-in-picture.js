export function supportsAnswerPictureInPicture(documentRef = document) {
  return Boolean(documentRef.defaultView?.documentPictureInPicture?.requestWindow)
}

export async function openAnswerPictureInPicture({
  documentRef = document,
  width = 460,
  height = 680,
  onClosed
} = {}) {
  const api = documentRef.defaultView?.documentPictureInPicture
  if (!api?.requestWindow) throw new Error('当前 Chrome 不支持置顶回答小窗')
  const pipWindow = await api.requestWindow({ width, height })
  pipWindow.document.documentElement.lang = 'zh-CN'
  pipWindow.document.title = '面试大师 · 实时回答'
  for (const node of documentRef.querySelectorAll('link[rel="stylesheet"], style')) {
    pipWindow.document.head.appendChild(node.cloneNode(true))
  }
  const root = pipWindow.document.createElement('div')
  root.id = 'interview-answer-pip-root'
  pipWindow.document.body.appendChild(root)
  pipWindow.addEventListener('pagehide', () => onClosed?.(), { once: true })
  return { window: pipWindow, root }
}
