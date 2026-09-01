import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const appVue = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const loginPageVue = readFileSync(new URL('./components/LoginPage.vue', import.meta.url), 'utf8')
const stylesCss = readFileSync(new URL('./styles.css', import.meta.url), 'utf8')

test('未登录入口不展示主题切换，登录后的账户菜单继续保留主题设置', () => {
  const guestBranch = appVue.match(/<template v-if="!authReady \|\| !authUser">([\s\S]*?)<\/template>\s*<section v-else-if="!navigationReady"/)?.[1]

  assert.ok(guestBranch, '应能定位未登录渲染分支')
  assert.doesNotMatch(guestBranch, /ThemeSwitcher|login-theme-switcher/)
  assert.match(appVue, /<div v-if="accountMenuSection === 'theme'" class="account-menu-panel account-theme-panel">\s*<ThemeSwitcher/)
})

test('登录页以求职助手和五段 Agent 执行轨介绍核心能力', () => {
  assert.match(loginPageVue, />求职助手</)
  assert.match(loginPageVue, /求职 Agent 为你持续推进/)
  assert.match(loginPageVue, /从一份简历/)
  assert.match(loginPageVue, /更合适的机会/)
  assert.match(loginPageVue, /简历分析/)
  assert.match(loginPageVue, /真实岗位获取/)
  assert.match(loginPageVue, /岗位匹配度分析/)
  assert.match(loginPageVue, /智能一键打招呼/)
  assert.match(loginPageVue, /实时面试助手/)
  assert.doesNotMatch(loginPageVue, /CAREER ORBIT SYSTEM/)
  assert.doesNotMatch(loginPageVue, /login-capability-detail|全流程协同/)
})

test('求职 Agent 执行轨使用有序列表和一条连续直线', () => {
  assert.match(
    loginPageVue,
    /<div class="login-agent-rail"[^>]*>[\s\S]*<ol class="login-agent-stations">[\s\S]*<li[\s\S]*v-for="item in careerCapabilities"/
  )
  assert.doesNotMatch(loginPageVue, /<button[\s\S]*class="login-route-step"/)

  const stationsRule = stylesCss.match(/\.login-agent-stations\s*\{([^}]*)\}/)?.[1]
  const trackRule = stylesCss.match(/\.login-agent-track\s*\{([^}]*)\}/)?.[1]
  assert.ok(stationsRule && trackRule, '应存在执行轨和五站等分布局')
  assert.match(stationsRule, /grid-template-columns:\s*repeat\(5,\s*minmax\(0,\s*1fr\)\)/)
  assert.match(trackRule, /left:\s*10%/)
  assert.match(trackRule, /right:\s*10%/)
})

test('执行轨具有克制动效并尊重减少动态效果设置', () => {
  assert.match(stylesCss, /@keyframes login-agent-orbit/)
  assert.match(stylesCss, /\.login-agent-orbit\s*\{[^}]*animation:\s*login-agent-orbit/s)
  assert.match(stylesCss, /@media \(prefers-reduced-motion:\s*reduce\)[\s\S]*\.login-agent-orbit/)
})

test('PC 登录页保持左右同屏并为缩放后的笔记本设置安全断点', () => {
  assert.match(
    loginPageVue,
    /<div class="login-career-stage">[\s\S]*class="login-career-copy"[\s\S]*class="login-agent-rail"[\s\S]*<\/div>\s*<\/section>/
  )

  const panelRule = stylesCss.match(/\.login-career-panel\s*\{([^}]*)\}/)?.[1]
  const stageRule = stylesCss.match(/\.login-career-stage\s*\{([^}]*)\}/)?.[1]

  assert.ok(panelRule && stageRule, '应存在紧凑执行轨布局的完整样式')
  assert.doesNotMatch(panelRule, /grid-template-rows:[^;]*1fr/)
  assert.match(stageRule, /max-width:\s*760px/)
  assert.match(stageRule, /place-self:\s*center/)
  assert.match(stylesCss, /@media \(max-width:\s*1023\.98px\)[\s\S]*\.login-career-panel\s*\{\s*display:\s*none;/)
  assert.match(stylesCss, /@media \(min-width:\s*1024px\) and \(max-width:\s*1279px\)/)
  assert.match(stylesCss, /@media \(max-height:\s*720px\) and \(min-width:\s*1024px\)/)
})
