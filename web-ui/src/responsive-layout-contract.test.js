import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const appVue = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const stylesCss = readFileSync(new URL('./styles.css', import.meta.url), 'utf8')

test('响应式壳层不再强制 1280px 页面宽度', () => {
  const bodyRule = stylesCss.match(/body\s*\{([^}]*)\}/)?.[1]

  assert.ok(bodyRule, '应存在 body 基础样式')
  assert.doesNotMatch(bodyRule, /min-width:\s*1280px/)
  assert.match(bodyRule, /min-width:\s*0/)
  assert.match(stylesCss, /\.main\s*\{[^}]*container:\s*app-main\s*\/\s*inline-size/s)
  assert.match(stylesCss, /\.main\s*\{[^}]*overflow-x:\s*clip/s)
})

test('响应式壳层统一使用 1024px 导航边界并保留紧凑桌面档', () => {
  assert.match(appVue, /matchMedia\('\(max-width: 1023\.98px\)'\)/)
  assert.match(
    stylesCss,
    /@media \(min-width:\s*1024px\) and \(max-width:\s*1279px\)[\s\S]*\.shell\s*\{[^}]*--app-sidebar-width:\s*216px/
  )
  assert.match(
    stylesCss,
    /@media \(max-width:\s*1023\.98px\)[\s\S]*\.mobile-nav-open \.sidebar\s*\{[^}]*translateX\(0\)/
  )
})

test('应用壳层使用动态视口高度并提供传统视口回退', () => {
  const shellRule = stylesCss.match(/\.shell\s*\{([^}]*)\}/)?.[1]
  const mainRule = stylesCss.match(/\.main\s*\{([^}]*)\}/)?.[1]

  assert.ok(shellRule && mainRule, '应存在 shell 与 main 基础样式')
  assert.match(shellRule, /height:\s*100vh/)
  assert.match(shellRule, /height:\s*100dvh/)
  assert.match(mainRule, /height:\s*100vh/)
  assert.match(mainRule, /height:\s*100dvh/)
})
