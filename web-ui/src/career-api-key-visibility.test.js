import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const careerAssistant = readFileSync(
  new URL('./components/CareerAssistantPage.vue', import.meta.url),
  'utf8'
)

test('API Key 输入框支持显示和隐藏且默认保持隐藏', () => {
  assert.match(careerAssistant, /const showModelApiKey = ref\(false\)/)
  assert.match(careerAssistant, /<label for="career-model-api-key">API Key/)
  assert.match(careerAssistant, /<input id="career-model-api-key"/)
  assert.match(careerAssistant, /:type="showModelApiKey \? 'text' : 'password'"/)
  assert.match(careerAssistant, /class="api-key-visibility-button"/)
  assert.match(careerAssistant, /@click="showModelApiKey = !showModelApiKey"/)
  assert.match(careerAssistant, /:aria-label="showModelApiKey \? '隐藏 API Key' : '显示 API Key'"/)
  assert.match(careerAssistant, /:aria-pressed="showModelApiKey"/)
  assert.match(careerAssistant, /\.connection-form-grid \.api-key-input-shell input \{ padding-right:46px; \}/)
})

test('切换模型连接上下文时重新隐藏 API Key', () => {
  assert.match(careerAssistant, /function hideModelApiKey\(\)\s*{\s*showModelApiKey\.value = false\s*}/)

  for (const functionName of [
    'closeModelDialog',
    'createModelConnection',
    'chooseProvider',
    'editModelConnection'
  ]) {
    const functionBody = careerAssistant.match(
      new RegExp(`function ${functionName}\\([^)]*\\) \\{[^]*?\\n\\}`)
    )?.[0]

    assert.ok(functionBody, `应能定位 ${functionName}`)
    assert.match(functionBody, /hideModelApiKey\(\)/, `${functionName} 应恢复密钥隐藏状态`)
  }
})
