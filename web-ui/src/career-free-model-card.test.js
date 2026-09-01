import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const careerAssistant = readFileSync(
  new URL('./components/CareerAssistantPage.vue', import.meta.url),
  'utf8'
)
const themeCss = readFileSync(new URL('./theme.css', import.meta.url), 'utf8')

test('每张免费模型卡片都提供所属服务商的官方 API Key 入口', () => {
  const template = careerAssistant.match(
    /<div v-for="model in offer\.models"[^]*?<\/div>\s*<\/div>\s*<footer>/
  )?.[0]

  assert.ok(template, '应能定位免费模型候选卡片模板')
  assert.match(template, /class="free-model-template-meta"/)
  assert.match(template, /:href="offer\.setup_url"/)
  assert.match(template, /target="_blank"/)
  assert.match(template, /rel="noopener noreferrer"/)
  assert.match(template, />获取 API Key ↗<\/a>/)
})

test('模型卡片内的 API Key 入口沿用蓝白主题强调色', () => {
  assert.match(themeCss, /\.free-model-template-meta a/)
})
