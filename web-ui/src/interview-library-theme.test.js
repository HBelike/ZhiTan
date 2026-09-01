import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const themeCss = readFileSync(new URL('./theme.css', import.meta.url), 'utf8')
const interviewLibrary = readFileSync(
  new URL('./components/InterviewLibraryPage.vue', import.meta.url),
  'utf8'
)

test('蓝白主题覆盖挂载到 body 的面经采集弹窗', () => {
  const collectionTheme = themeCss.match(
    /\/\* 面经采集弹窗：[^]*?\*\/([^]*?)\/\* 登录页/
  )?.[1]

  assert.ok(collectionTheme, '应提供独立的面经采集弹窗蓝色主题覆盖')

  for (const selector of [
    '.collection-backdrop',
    '.dialog-success',
    '.collection-option-toggle input',
    '.collection-human-review-note',
    '.xiaohongshu-job-card',
    '.collection-stage-track span.active i',
    '.collection-summary-grid strong',
    '.candidate-status.is-valid',
    '.candidate-status.is-imported'
  ]) {
    assert.match(collectionTheme, new RegExp(selector.replaceAll('.', '\\.')))
  }

  assert.match(collectionTheme, /accent-color:\s*var\(--ui-accent\)/)
  assert.match(collectionTheme, /background:\s*var\(--ui-surface-active\)/)
  assert.match(collectionTheme, /color:\s*var\(--ui-accent-ink\)/)
})

test('蓝白主题覆盖面经文件导入弹窗的上传、解析和完成状态', () => {
  const importTheme = themeCss.match(
    /\/\* 面经文件导入弹窗：[^]*?\*\/([^]*?)\/\* 面经采集弹窗/
  )?.[1]

  assert.ok(importTheme, '应提供独立的面经文件导入弹窗蓝色主题覆盖')

  for (const selector of [
    '.import-backdrop',
    '.import-dialog',
    '.file-intake-intro',
    '.file-picker',
    '.file-import-strategy',
    '.file-import-strategy input',
    '.recognition-card',
    '.file-parse-overlay',
    '.file-parse-progress-card',
    '.progress-track i',
    '.file-progress-list .is-parsing'
  ]) {
    assert.match(importTheme, new RegExp(selector.replaceAll('.', '\\.')))
  }

  assert.match(importTheme, /accent-color:\s*var\(--ui-accent\)/)
  assert.match(importTheme, /background:\s*var\(--ui-surface-active\)/)
  assert.match(importTheme, /color:\s*var\(--ui-accent-ink\)/)
})

test('面经库只保留空态中的文件导入主入口', () => {
  assert.doesNotMatch(
    interviewLibrary,
    /class="primary-action"\s+@click="openImport\('file'\)">导入材料<\/button>/
  )
  assert.match(
    interviewLibrary,
    /@click="openImport\('file'\)">导入第一份材料<\/button>/
  )
})

test('面经库顶部导入区使用独立盒子并让按钮名称与功能一致', () => {
  const header = interviewLibrary.match(
    /<header class="library-header">([^]*?)<\/header>/
  )?.[1]

  assert.ok(header, '应保留独立的面经库顶部导入盒')
  assert.match(header, /class="library-heading-mark"/)
  assert.match(header, /<strong>导入与收录<\/strong>/)
  assert.match(header, /@click="openCollection\('xiaohongshu'\)"[^>]*>[^]*?小红书URL读取<\/button>/)
  assert.match(header, /@click="openImport\('file'\)"[^>]*>[^]*?上传面经<\/button>/)
  assert.doesNotMatch(header, /小红书公开内容导入|粘贴正文|openImport\('text'\)/)

  assert.match(interviewLibrary, /\.library-header \{[^}]*border:\s*1px solid/)
  assert.match(interviewLibrary, /\.library-heading-mark \{/)
  assert.match(interviewLibrary, /\.library-actions \.primary-action \{/)
})
