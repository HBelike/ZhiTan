const IMAGE_PREVIEW_LABEL = '预览图片'

export function decorateOpenableImages(container) {
  if (!container?.querySelectorAll) return

  container.querySelectorAll('img').forEach((image) => {
    const imageUrl = image.getAttribute?.('src') || image.currentSrc || image.src
    if (!imageUrl) return

    let link = image.closest?.('a')
    if (!link) {
      link = image.ownerDocument.createElement('a')
      image.replaceWith(link)
      link.append(image)
    }

    link.classList.add('article-image-link')
    link.setAttribute('href', imageUrl)
    link.removeAttribute('target')
    link.removeAttribute('rel')
    link.setAttribute('data-image-preview-url', imageUrl)
    link.setAttribute('aria-label', IMAGE_PREVIEW_LABEL)
    link.setAttribute('title', '点击预览图片')
  })
}

export function resolveImagePreviewRequest(target) {
  const link = target?.closest?.('a.article-image-link')
  if (!link) return null

  const image = link.querySelector?.('img')
  const source = link.getAttribute?.('data-image-preview-url') || link.getAttribute?.('href')
  if (!source) return null

  return {
    source,
    alt: image?.getAttribute?.('alt')?.trim() || '文章图片'
  }
}
