import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const appVue = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const stylesCss = readFileSync(new URL('./styles.css', import.meta.url), 'utf8')
const themeCss = readFileSync(new URL('./theme.css', import.meta.url), 'utf8')

test('应用左上角使用求职助手机会路径品牌标识', () => {
  const brandMarkup = appVue.match(/<div class="brand">([\s\S]*?)<\/div>\s*<\/div>\s*<nav class="nav-list"/)?.[1]
  const brandIconRule = stylesCss.match(/\.brand-icon\s*\{([^}]*)\}/)?.[1]

  assert.ok(brandMarkup, '应存在应用左上角品牌区')
  assert.match(brandMarkup, /class="brand-icon" aria-hidden="true"/)
  assert.match(brandMarkup, /<svg viewBox="0 0 48 48"/)
  assert.match(brandMarkup, /<strong>求职助手<\/strong>/)
  assert.match(brandMarkup, /<span>让机会更近一步<\/span>/)
  assert.doesNotMatch(brandMarkup, /Find Job|Agent 内容工坊|>AI</)

  assert.ok(brandIconRule, '应存在品牌图标样式')
  assert.match(brandIconRule, /border-radius:\s*11px 20px 11px 20px/)
  assert.match(brandIconRule, /linear-gradient\(145deg, var\(--ui-brand-deep\), var\(--ui-accent\)\)/)
  assert.match(stylesCss, /\.brand-icon svg\s*\{[^}]*width:\s*34px;[^}]*height:\s*34px;/s)
})

test('机会路径 Logo 跟随蓝色和绿色主题切换渐变', () => {
  const blueTheme = themeCss.match(/:root\[data-ui-theme="blue"\]\s*\{([^}]*)\}/)?.[1]
  const greenTheme = themeCss.match(/:root\[data-ui-theme="green"\]\s*\{([^}]*)\}/)?.[1]

  assert.ok(blueTheme && greenTheme, '应同时存在蓝色和绿色主题变量')
  assert.match(blueTheme, /--ui-brand-deep:\s*#17345b/)
  assert.match(blueTheme, /--ui-brand-shadow:\s*rgba\(23, 52, 91, 0\.18\)/)
  assert.match(greenTheme, /--ui-brand-deep:\s*#395225/)
  assert.match(greenTheme, /--ui-brand-shadow:\s*rgba\(57, 82, 37, 0\.18\)/)
})
