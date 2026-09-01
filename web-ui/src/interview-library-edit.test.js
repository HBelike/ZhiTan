import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const component = readFileSync(
  new URL('./components/InterviewLibraryPage.vue', import.meta.url),
  'utf8'
)
const themeCss = readFileSync(new URL('./theme.css', import.meta.url), 'utf8')

test('面经编辑支持公司名称和面试岗位并在保存后刷新树', () => {
  assert.match(component, /const editorCompanyName = ref\(''\)/)
  assert.match(component, /const editorRoleName = ref\(''\)/)
  assert.match(component, /<span>公司名称<\/span>/)
  assert.match(component, /<span>面试岗位<\/span>/)
  assert.match(component, /company_name:\s*editorCompanyName\.value\.trim\(\)/)
  assert.match(component, /role_name:\s*editorRoleName\.value\.trim\(\)/)
  assert.match(component, /await loadTree\(\)/)
})

test('编辑态和预览态使用同一正文宽度且不显示英文装饰眉题', () => {
  assert.match(component, /\.editor-identity-panel,[^]*?\.editor-support-panel\s*\{[^}]*width:\s*min\(900px,\s*100%\)/)
  assert.match(component, /\.editor-layout\s*\{[^}]*width:\s*100%/)
  assert.doesNotMatch(component, /CONTENT EDITOR/)
})

test('蓝色主题完整覆盖编辑归属、正文、字数和补充信息', () => {
  const editorTheme = themeCss.match(
    /\/\* 面经编辑器：[^]*?\*\/([^]*?)(?:\/\* 面经文件导入弹窗|$)/
  )?.[1]

  assert.ok(editorTheme, '应提供独立的面经编辑器蓝色主题覆盖')
  for (const selector of [
    '.editor-identity-panel',
    '.editor-field input',
    '.editor-canvas textarea',
    '.editor-canvas > span em',
    '.editor-support-panel summary'
  ]) {
    assert.match(editorTheme, new RegExp(selector.replaceAll('.', '\\.')))
  }
  assert.match(editorTheme, /color:\s*var\(--ui-accent-ink\)/)
  assert.match(editorTheme, /border-color:\s*var\(--ui-line\)/)
})

test('左右主标题统一显示面试岗位并使用最近保存时间', () => {
  assert.match(component, /function experienceSavedAt\(experience\)/)
  assert.match(component, /experience\?\.updated_at\s*\|\|\s*experience\?\.created_at/)
  assert.match(component, /<strong>\{\{ experience\.role_name \}\}<\/strong>/)
  assert.match(component, /<small>更新于 \{\{ formatUpdatedAt\(experienceSavedAt\(experience\)\) \}\}<\/small>/)
  assert.match(component, /<h3>\{\{ selectedExperience\.role_name \}\}<\/h3>/)
  assert.match(component, /<span>更新于 \{\{ formatUpdatedAt\(experienceSavedAt\(selectedExperience\)\) \}\}<\/span>/)
  assert.doesNotMatch(component, /selectedExperience\.job_name/)
  assert.doesNotMatch(component, /payload\.job_name/)
  assert.doesNotMatch(component, /formatDate\(selectedExperience\.interview_date\)/)
})

test('面经详情支持二次确认后永久删除并刷新树', () => {
  assert.match(component, /const deleting = ref\(false\)/)
  assert.match(component, /async function deleteExperience\(\)/)
  assert.match(component, /window\.confirm\([^]*永久删除/)
  assert.match(component, /method:\s*'DELETE'/)
  assert.match(component, /面经已删除/)
  assert.match(component, /class="danger-action"[^>]*@click="deleteExperience"/)
})

test('公开面经根据服务端写权限进入可编辑或只读状态', () => {
  assert.match(component, /const canWriteSelected = computed\(\(\) => selectedExperience\.value\?\.can_write === true\)/)
  assert.match(component, /v-if="!editMode && canWriteSelected"/)
  assert.match(component, /公开面经，仅创建者或管理员可维护/)
  assert.match(component, /if \(!selectedExperience\.value \|\| !canWriteSelected\.value\) return/)
  assert.match(component, /if \(!experience \|\| !canWriteSelected\.value \|\| deleting\.value\) return/)
})

test('公开信息收集界面默认 10 条且最多分析 10 条', () => {
  assert.match(component, /mode === 'keyword'[^\n]*requestedLimit = 10/)
  assert.match(component, /默认 10 条/)
  assert.match(component, /最多分析数量[^]*max="10"/)
  assert.match(component, /可设置 5–10 条/)
})
