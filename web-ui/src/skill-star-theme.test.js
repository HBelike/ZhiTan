import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const themeCss = readFileSync(new URL('./theme.css', import.meta.url), 'utf8')
const appVue = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')

test('蓝白主题覆盖 Skill Star 圆环和 Teleport 浮层', () => {
  const starTheme = themeCss.match(
    /\/\* Skill Star 统计：[^]*?\*\/([^]*?)\/\* 求职对话页/
  )?.[1]

  assert.ok(starTheme, '应提供独立的 Skill Star 蓝色主题覆盖')

  for (const selector of [
    '.star-stat-trigger:hover',
    '.star-ring',
    '.star-stat-trigger.empty .star-ring',
    '.skill-star-popover',
    '.skill-star-popover-heading',
    '.skill-star-popover-heading p',
    '.skill-star-popover-ring',
    '.skill-star-popover-ring.empty',
    '.skill-star-popover-ring strong',
    '.skill-star-popover-note'
  ]) {
    assert.match(starTheme, new RegExp(selector.replaceAll('.', '\\.')))
  }

  assert.match(starTheme, /var\(--ui-accent\)/)
  assert.match(starTheme, /var\(--ui-surface-active\)/)
  assert.match(starTheme, /var\(--ui-accent-ink\)/)
})

test('无 GitHub 仓库的 Skill 不渲染空 Star 统计入口', () => {
  assert.match(
    appVue,
    /<small v-if="skill\.repository_full_name" class="skill-star-line">/
  )
  assert.match(
    appVue,
    /<small v-else class="skill-local-line">本地 Skill<\/small>/
  )
  assert.match(
    appVue,
    /<button\s+v-if="skill\.repository_full_name"\s+type="button"\s+class="star-stat-trigger"/
  )
})
