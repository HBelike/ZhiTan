import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildXiaohongshuCanonicalUrl,
  buildXiaohongshuSearchUrl,
  classifyXiaohongshuPageState,
  normalizeXiaohongshuNote,
  normalizeXiaohongshuSearchCards
} from '../xiaohongshu-data.js'

const noteId = '6a4a58ff000000002101659d'

test('搜索 URL 编码关键词且不依赖 AI 搜索页', () => {
  const url = new URL(buildXiaohongshuSearchUrl('全栈 开发面经'))
  assert.equal(url.pathname, '/search_result')
  assert.equal(url.searchParams.get('keyword'), '全栈 开发面经')
  assert.equal(url.pathname.includes('_ai'), false)
})

test('搜索卡片按 note_id 去重并保留当前调用的签名 URL', () => {
  const cards = normalizeXiaohongshuSearchCards([
    {
      signedUrl: `https://www.xiaohongshu.com/search_result/${noteId}?xsec_token=temporary`,
      title: '大疆前端面经',
      authorName: '前端barby'
    },
    { signedUrl: `https://www.xiaohongshu.com/explore/${noteId}?xsec_token=new-token` }
  ], 20)
  assert.equal(cards.length, 1)
  assert.equal(cards[0].noteId, noteId)
  assert.equal(cards[0].canonicalUrl, buildXiaohongshuCanonicalUrl(noteId))
  assert.match(cards[0].signedUrl, /xsec_token=temporary/)
})

test('详情规范化不返回签名详情 URL且只保留可信图片域名', () => {
  const detail = normalizeXiaohongshuNote({
    noteId,
    title: '全栈面经',
    bodyText: '一面：介绍一下项目。',
    tags: ['#面试', { name: '全栈' }],
    imageUrls: [
      'https://sns-webpic-qc.xhscdn.com/example.webp?token=image-only',
      'data:image/png;base64,abc',
      'https://evil.example/image.png'
    ],
    signedUrl: `https://www.xiaohongshu.com/explore/${noteId}?xsec_token=secret`
  })
  assert.equal(detail.canonicalUrl, `https://www.xiaohongshu.com/explore/${noteId}`)
  assert.deepEqual(detail.tags, ['面试', '全栈'])
  assert.deepEqual(detail.imageUrls, ['https://sns-webpic-qc.xhscdn.com/example.webp?token=image-only'])
  assert.equal('signedUrl' in detail, false)
})

test('登录、验证和限流都要求暂停整批', () => {
  assert.equal(classifyXiaohongshuPageState({ url: 'https://www.xiaohongshu.com/login' }).code, 'login_required')
  assert.equal(classifyXiaohongshuPageState({ text: '请完成安全验证' }).code, 'verification_required')
  assert.equal(classifyXiaohongshuPageState({ status: 429 }).code, 'rate_limited')
  assert.equal(classifyXiaohongshuPageState({ text: '正常笔记正文' }), null)
})

test('Web 端 300031 和 App 扫码限制只跳过当前笔记', () => {
  const byCode = classifyXiaohongshuPageState({
    url: 'https://www.xiaohongshu.com/404?error_code=300031&error_msg=当前笔记暂时无法浏览'
  })
  assert.equal(byCode.code, 'content_restricted')
  assert.equal(byCode.stopBatch, false)
  assert.equal(byCode.retryable, false)

  const byPage = classifyXiaohongshuPageState({
    text: '当前笔记暂时无法浏览 请打开小红书App扫码查看'
  })
  assert.equal(byPage.code, 'content_restricted')
  assert.equal(byPage.stopBatch, false)
})
