export async function readXiaohongshuSearchPage(limit = 20) {
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))
  const requestedLimit = Math.max(5, Math.min(50, Number(limit) || 20))
  const notePattern = /\/(?:explore|search_result)\/([0-9a-z]{24})(?:[/?#]|$)/i

  function collect() {
    const items = []
    const seen = new Set()
    for (const anchor of document.querySelectorAll('a[href*="/explore/"],a[href*="/search_result/"]')) {
      const signedUrl = anchor.href || anchor.getAttribute('href') || ''
      const noteId = signedUrl.match(notePattern)?.[1] ?? ''
      if (!noteId || seen.has(noteId)) continue
      const card = anchor.closest('section,article,[class*="note-item"],[class*="feed-card"]')
        || anchor.parentElement?.parentElement
        || anchor
      const titleElement = card.querySelector?.('[class*="title"],.title,[data-v-a264b01a]')
      const authorElement = card.querySelector?.('[class*="author"] [class*="name"],[class*="author"],.name')
      const likeElement = card.querySelector?.('[class*="like"] [class*="count"],[class*="like"],.count')
      const image = card.querySelector?.('img')
      const title = titleElement?.textContent?.trim()
        || anchor.getAttribute('title')?.trim()
        || card.textContent?.trim().split('\n').find((part) => part.trim().length > 3)
        || ''
      seen.add(noteId)
      items.push({
        noteId,
        signedUrl,
        title,
        authorName: authorElement?.textContent?.trim() || '',
        likedCount: likeElement?.textContent?.trim() || '',
        coverUrl: image?.currentSrc || image?.src || image?.getAttribute('data-src') || ''
      })
    }
    return items
  }

  let cards = []
  let unchangedRounds = 0
  for (let round = 0; round < 16; round += 1) {
    cards = collect()
    if (cards.length >= requestedLimit) break
    const before = cards.length
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' })
    await sleep(650)
    const after = collect().length
    unchangedRounds = after <= before ? unchangedRounds + 1 : 0
    if (unchangedRounds >= 3) break
  }

  return {
    url: window.location.href,
    pageText: document.body?.innerText?.slice(0, 12000) || '',
    cards: collect().slice(0, requestedLimit)
  }
}

export async function openXiaohongshuNoteFromSearchPage(expectedNoteId) {
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))
  const noteId = String(expectedNoteId ?? '').trim()
  const notePattern = /\/(?:explore|search_result)\/([0-9a-z]{24})(?:[/?#]|$)/i
  const anchors = Array.from(
    document.querySelectorAll('a[href*="/explore/"],a[href*="/search_result/"]')
  )
  const anchor = anchors.find((item) => {
    const href = item.href || item.getAttribute('href') || ''
    return href.match(notePattern)?.[1] === noteId
  })
  if (!anchor) {
    return {
      clicked: false,
      opened: false,
      url: window.location.href,
      pageText: document.body?.innerText?.slice(0, 12000) || ''
    }
  }

  anchor.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' })
  await sleep(180)
  const originalTarget = anchor.getAttribute('target')
  anchor.removeAttribute('target')
  anchor.click()
  if (originalTarget !== null) anchor.setAttribute('target', originalTarget)
  return {
    clicked: true,
    url: window.location.href
  }
}

export async function restoreXiaohongshuSearchPage(expectedSearchUrl) {
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))
  const target = String(expectedSearchUrl ?? '')

  function isSearchReady() {
    const current = window.location.href
    const sameSearch = current.startsWith(target.split('#')[0])
      || window.location.pathname === '/search_result'
    return sameSearch && Boolean(
      document.querySelector('a[href*="/explore/"],a[href*="/search_result/"]')
    )
  }

  if (isSearchReady()) return { restored: true, url: window.location.href }
  window.history.back()
  for (let attempt = 0; attempt < 48; attempt += 1) {
    if (isSearchReady()) return { restored: true, url: window.location.href }
    await sleep(250)
  }
  return { restored: false, url: window.location.href }
}

export async function readXiaohongshuNotePage(expectedNoteId) {
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))
  const noteId = String(expectedNoteId ?? '').trim()

  function stringValue(value) {
    if (typeof value === 'string' || typeof value === 'number') return String(value).trim()
    return ''
  }

  function candidateId(value) {
    return stringValue(value?.noteId || value?.note_id || value?.id || value?.noteIdStr)
  }

  function findEntity(root) {
    if (!root || typeof root !== 'object') return null
    const stack = [root]
    const seen = new WeakSet()
    let inspected = 0
    let fallback = null
    while (stack.length && inspected < 50000) {
      const value = stack.pop()
      if (!value || typeof value !== 'object' || seen.has(value)) continue
      seen.add(value)
      inspected += 1
      const id = candidateId(value)
      const hasContent = Boolean(value.desc || value.description || value.content || value.imageList || value.images)
      if (id === noteId && hasContent) return value.noteCard || value.note || value
      if (!fallback && hasContent && (value.noteCard || value.note)) fallback = value.noteCard || value.note
      for (const nested of Object.values(value)) {
        if (nested && typeof nested === 'object') stack.push(nested)
      }
    }
    return fallback
  }

  function imageUrls(entity) {
    const result = []
    const seen = new Set()
    const stack = [entity?.imageList, entity?.images, entity?.image_list]
    while (stack.length) {
      const value = stack.pop()
      if (Array.isArray(value)) {
        for (const item of value) stack.push(item)
        continue
      }
      if (!value || typeof value !== 'object') continue
      for (const key of ['urlDefault', 'urlPre', 'url', 'original', 'imageUrl']) {
        const url = stringValue(value[key])
        if (url && !seen.has(url)) {
          seen.add(url)
          result.push(url)
        }
      }
      for (const key of ['urlList', 'infoList']) {
        if (value[key]) stack.push(value[key])
      }
    }
    if (!result.length) {
      const scope = document.querySelector('[class*="note-content"],[class*="swiper"],[class*="slider"]') || document
      for (const image of scope.querySelectorAll('img')) {
        const url = image.currentSrc || image.src || image.getAttribute('data-src') || ''
        if (url && !seen.has(url)) {
          seen.add(url)
          result.push(url)
        }
      }
    }
    return result
  }

  function tags(entity) {
    const values = entity?.tagList || entity?.tags || entity?.tag_list || []
    return (Array.isArray(values) ? values : [])
      .map((item) => stringValue(typeof item === 'object' ? item.name || item.title : item))
      .filter(Boolean)
  }

  let entity = null
  for (let attempt = 0; attempt < 40; attempt += 1) {
    entity = findEntity(window.__INITIAL_STATE__ || window.__INITIAL_SSR_STATE__ || window.__PINIA__)
    const visibleBody = document.querySelector('#detail-desc,[class*="note-content"] [class*="desc"],.desc')?.textContent?.trim()
    if (entity || visibleBody) break
    await sleep(250)
  }

  const titleElement = document.querySelector('#detail-title,[class*="note-content"] [class*="title"],h1.title')
  const bodyElement = document.querySelector('#detail-desc,[class*="note-content"] [class*="desc"],.desc')
  const authorElement = document.querySelector('[class*="author"] [class*="name"],[class*="author-name"]')
  return {
    url: window.location.href,
    pageText: document.body?.innerText?.slice(0, 12000) || '',
    noteId,
    title: stringValue(entity?.title || entity?.displayTitle || entity?.noteTitle)
      || titleElement?.textContent?.trim()
      || document.querySelector('meta[property="og:title"]')?.content
      || document.title,
    bodyText: stringValue(entity?.desc || entity?.description || entity?.content || entity?.noteContent)
      || bodyElement?.textContent?.trim()
      || document.querySelector('meta[property="og:description"]')?.content
      || '',
    authorName: stringValue(entity?.user?.nickname || entity?.user?.name || entity?.author?.nickname)
      || authorElement?.textContent?.trim()
      || '',
    publishedAt: stringValue(entity?.time || entity?.publishTime || entity?.createTime),
    tags: tags(entity),
    imageUrls: imageUrls(entity)
  }
}
