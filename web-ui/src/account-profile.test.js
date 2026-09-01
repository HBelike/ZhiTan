import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const appVue = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const stylesCss = readFileSync(new URL('./styles.css', import.meta.url), 'utf8')

test('个人信息区按名称、账号和角色展示当前用户资料', () => {
  assert.match(
    appVue,
    /const accountIdentifier = computed\(\(\) => authUser\.value\?\.email \|\| '未绑定'\)/
  )
  assert.match(
    appVue,
    /<div><span>名称<\/span><strong>\{\{ accountDisplayName \}\}<\/strong><\/div>/
  )
  assert.match(
    appVue,
    /<div class="account-identifier-row"><span>账号<\/span><strong :title="accountIdentifier">\{\{ accountIdentifier \}\}<\/strong><\/div>/
  )
  assert.match(
    appVue,
    /<div><span>角色<\/span><strong>\{\{ accountRoleLabel \}\}<\/strong><\/div>/
  )
})

test('长账号在个人信息区完整换行展示', () => {
  const identifierRule = stylesCss.match(/\.account-identifier-row strong\s*\{([^}]*)\}/)?.[1]

  assert.ok(identifierRule, '应为账号值提供独立样式')
  assert.match(identifierRule, /overflow-wrap:\s*anywhere/)
  assert.match(identifierRule, /white-space:\s*normal/)
  assert.match(identifierRule, /text-align:\s*right/)
})
