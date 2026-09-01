import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const styles = readFileSync(new URL('./styles.css', import.meta.url), 'utf8')
const appVue = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')

test('所有路由共用独立的顶部标题盒', () => {
  const topbar = styles.match(/\n\.topbar \{([^}]*)\}/)?.[1]

  assert.ok(topbar, '应存在全局顶部标题盒样式')
  assert.match(topbar, /border:\s*1px solid var\(--ui-line\)/)
  assert.match(topbar, /border-radius:\s*12px/)
  assert.match(topbar, /background:\s*var\(--ui-surface\)/)
  assert.match(topbar, /box-shadow:\s*var\(--ui-shadow\)/)
  assert.match(appVue, /<header class="topbar">/)
})
