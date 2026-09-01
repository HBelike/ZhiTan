import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentUrl = new URL('./components/CareerAssistantPage.vue', import.meta.url)

function functionSource(source, name) {
  const start = source.indexOf(`function ${name}`)
  assert.notEqual(start, -1, `未找到函数 ${name}`)
  const boundary = /\r?\n}\r?\n/g
  boundary.lastIndex = start
  const match = boundary.exec(source)
  assert.ok(match, `函数 ${name} 缺少结束边界`)
  return source.slice(start, match.index + match[0].length)
}

test('@ 只检索面经，/ 才能唤醒 Skill', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const activeSkillInvocation = functionSource(source, 'activeSkillInvocation')
  const selectSkillMention = functionSource(source, 'selectSkillMention')
  const removeSkillInvocation = functionSource(source, 'removeSkillInvocation')
  const skillInvocationPresent = functionSource(source, 'skillInvocationPresent')

  assert.doesNotMatch(activeSkillInvocation, /\[@\/\]/)
  assert.ok(activeSkillInvocation.includes('text.match(/(?:^|\\s)\\/('))
  assert.match(selectSkillMention, /const trigger = '\/'/)
  assert.doesNotMatch(selectSkillMention, /\[@\/\]/)
  assert.doesNotMatch(removeSkillInvocation, /\[@\/\]/)
  assert.doesNotMatch(skillInvocationPresent, /\[@\/\]/)
  assert.match(source, /输入 @ 可引用面经，输入 \/ 可调用 Skill/)
  assert.match(source, /输入你想咨询的问题…/)
  assert.doesNotMatch(source, /基于当前简历与目标岗位提问/)
})

test('已选 Skill 与面经在正文内使用不同颜色高亮', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const syncComposerHighlightInput = functionSource(source, 'syncComposerHighlightInput')

  assert.match(source, /class="composer-input-shell"/)
  assert.match(source, /class="composer-highlight-layer"/)
  assert.match(source, /composer-highlight-skill/)
  assert.match(source, /composer-highlight-interview/)
  assert.match(source, /--composer-skill-ink:\s*#0869d8/)
  assert.match(source, /--composer-interview-ink:\s*#b45309/)
  assert.match(source, /@input="syncComposerHighlightInput"/)
  assert.match(syncComposerHighlightInput, /composerDisplayText\.value = event\.currentTarget\.value/)
  assert.match(source, /\.composer-input-shell textarea \{[^}]*color:#324032/)
  assert.match(source, /\.composer-highlight-layer \{[^}]*z-index:3[^}]*background:transparent[^}]*color:transparent/)
  assert.doesNotMatch(source, /-webkit-text-fill-color:transparent/)
  assert.doesNotMatch(source, /aria-label="已引用面经"/)
  assert.doesNotMatch(source, /aria-label="待挂载 Skill"/)
})

test('会话工具入口保持紧凑且归档归入历史记录操作', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const archiveConversation = functionSource(source, 'archiveConversation')

  assert.doesNotMatch(source, /<span>简历资料<\/span>/)
  assert.match(source, /return completed \? `简历上传 \$\{completed\}\/2` : '简历上传'/)
  assert.match(source, /<option v-if="hasReadyFreeModel" value="free_quota_first">【免费】自动选择<\/option>/)
  assert.match(source, /costTier: 'paid'/)
  assert.match(source, /readyPaidModelProfiles/)
  assert.match(source, /label="已接入的付费模型"/)
  assert.match(source, /【付费】\{\{ modelChoiceLabel\(item\) \}\}/)
  assert.match(source, /\{\{ costTierText\(item\.profile\.cost_tier\) \}\} · 顺序/)
  assert.match(source, /:title="item\.blocked_reason \|\| ''"/)
  assert.doesNotMatch(source, /已配置的其他模型/)
  assert.match(source, />\{\{ hasReadyModel \? '免费模型' : '配置模型' \}\}<\/button>/)
  assert.match(source, /class="quiet-button model-manager-button"[^>]*>模型连接<\/button>/)
  assert.match(source, /class="conversation-action-options"[\s\S]*?archiveConversation\(conversation\)[\s\S]*?删除会话/)
  assert.doesNotMatch(source, /class="quiet-button danger"[^>]*archiveConversation/)
  assert.match(archiveConversation, /function archiveConversation\(conversation\)/)
  assert.ok(archiveConversation.includes('/api/career/conversations/${conversation.id}/archive'))
  assert.match(archiveConversation, /selectedConversation\.value\?\.id === conversation\.id/)
})
