import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const loginPageVue = readFileSync(new URL('./components/LoginPage.vue', import.meta.url), 'utf8')

test('bootstrap status drives provider-dependent authentication controls', () => {
  assert.match(loginPageVue, /emailAuthEnabled\.value = Boolean\(payload\.email_auth_enabled\)/)
  assert.match(loginPageVue, /loginMethod\.value = 'password'/)
  assert.match(loginPageVue, /requiresBootstrap\.value && \(cliBootstrapOnly\.value \|\| !emailAuthEnabled\.value\)/)
})
