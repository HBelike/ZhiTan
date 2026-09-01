import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildComposerHighlightSegments,
  composerReferencePresent
} from './career-composer-highlights.js'

const selectedSkill = { id: 'skill-1', name: 'find-skills' }
const selectedInterview = {
  id: 'interview-1',
  company_name: '元响科技',
  role_name: '全栈开发工程师'
}

test('只高亮已经选中的 Skill 和面经，普通正文保持原样', () => {
  const segments = buildComposerHighlightSegments(
    '/find-skills 帮我结合 @元响科技·全栈开发工程师 准备面试，/not-selected 保持普通文本',
    [selectedSkill],
    [selectedInterview]
  )

  assert.deepEqual(segments, [
    { kind: 'skill', text: '/find-skills' },
    { kind: 'plain', text: ' 帮我结合 ' },
    { kind: 'interview', text: '@元响科技·全栈开发工程师' },
    { kind: 'plain', text: ' 准备面试，/not-selected 保持普通文本' }
  ])
})

test('高亮词必须是完整引用，不能命中正文中的同名片段', () => {
  const segments = buildComposerHighlightSegments(
    '前缀/find-skills /find-skills-extra',
    [selectedSkill],
    []
  )

  assert.deepEqual(segments, [
    { kind: 'plain', text: '前缀/find-skills /find-skills-extra' }
  ])
})

test('删除正文中的引用词后可识别为不再挂载', () => {
  assert.equal(composerReferencePresent('/find-skills 请分析', '/find-skills'), true)
  assert.equal(composerReferencePresent('请分析', '/find-skills'), false)
  assert.equal(
    composerReferencePresent('参考 @元响科技·全栈开发工程师 回答', '@元响科技·全栈开发工程师'),
    true
  )
})
