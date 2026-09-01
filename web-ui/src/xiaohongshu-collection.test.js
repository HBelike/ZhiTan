import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createXiaohongshuDiscoveryPayload,
  createXiaohongshuNotePayload,
  hasXiaohongshuCollectionCapability,
  isXiaohongshuAccountBlockingError,
  normalizeXiaohongshuCollectionLimit,
  selectXiaohongshuCardsToProcess
} from './xiaohongshu-collection.js'

const noteId = '6a4a58ff000000002101659d'

test('关键词数量默认 20 且限制在 5 到 50', () => {
  assert.equal(normalizeXiaohongshuCollectionLimit(''), 20)
  assert.equal(normalizeXiaohongshuCollectionLimit(1), 5)
  assert.equal(normalizeXiaohongshuCollectionLimit(99), 50)
})

test('发现与详情请求不携带签名详情地址或封面', () => {
  const card = {
    noteId,
    signedUrl: `https://www.xiaohongshu.com/explore/${noteId}?xsec_token=secret`,
    coverUrl: 'https://xhscdn.com/cover.jpg',
    title: '全栈面经',
    authorName: '作者'
  }
  const discovery = createXiaohongshuDiscoveryPayload([card])
  assert.deepEqual(discovery.items[0], {
    note_id: noteId,
    title: '全栈面经',
    author_name: '作者',
    liked_count: null
  })
  assert.doesNotMatch(JSON.stringify(discovery), /xsec_token|coverUrl/)

  const note = createXiaohongshuNotePayload({ ...card, bodyText: '1. 项目难点？', imageUrls: ['https://xhscdn.com/1.png'] })
  assert.equal(note.note_id, noteId)
  assert.equal(note.body_text, '1. 项目难点？')
  assert.doesNotMatch(JSON.stringify(note), /xsec_token|signedUrl/)
})

test('单篇 Web 访问受限会随候选记录但不携带签名地址', () => {
  const payload = createXiaohongshuNotePayload({
    noteId,
    sourceErrorCode: 'content_restricted',
    sourceErrorMessage: '当前仅允许在 App 查看',
    signedUrl: `https://www.xiaohongshu.com/explore/${noteId}?xsec_token=secret`
  })
  assert.equal(payload.source_error_code, 'content_restricted')
  assert.match(payload.source_error_message, /App/)
  assert.doesNotMatch(JSON.stringify(payload), /xsec_token/)
})

test('恢复时只处理仍处于 discovered 的卡片', () => {
  const cards = [{ noteId }, { noteId: '7b4a58ff000000002101659e' }]
  const candidates = [
    { status: 'imported', metadata: { note_id: noteId } },
    { status: 'discovered', metadata: { note_id: '7b4a58ff000000002101659e' } }
  ]
  assert.deepEqual(selectXiaohongshuCardsToProcess(cards, candidates).map((item) => item.noteId), ['7b4a58ff000000002101659e'])
})

test('扩展能力和账号级错误使用明确门禁', () => {
  assert.equal(hasXiaohongshuCollectionCapability({ capabilities: ['xiaohongshu_keyword_collection'] }), true)
  assert.equal(hasXiaohongshuCollectionCapability({ capabilities: [] }), false)
  assert.equal(isXiaohongshuAccountBlockingError({ code: 'verification_required' }), true)
  assert.equal(isXiaohongshuAccountBlockingError({ code: 'search_context_lost', stopBatch: true }), true)
  assert.equal(isXiaohongshuAccountBlockingError({ code: 'note_parse_failed' }), false)
})
