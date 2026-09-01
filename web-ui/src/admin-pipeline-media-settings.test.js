import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import {
  mediaSettingsForPayload,
  mediaSettingsFromConfig
} from './admin-pipeline-media-settings.js'

test('旧配置使用现有图片、视频与语音生成默认值', () => {
  assert.deepEqual(mediaSettingsFromConfig({}), {
    image_generation_enabled: true,
    video_generation_enabled: false,
    audio_generation_enabled: false
  })
})

test('读取和保存时保留管理员选择的媒体开关', () => {
  const saved = mediaSettingsFromConfig({
    image_generation_enabled: false,
    video_generation_enabled: true,
    audio_generation_enabled: true
  })

  assert.deepEqual(mediaSettingsForPayload(saved), {
    image_generation_enabled: false,
    video_generation_enabled: true,
    audio_generation_enabled: true
  })
})

test('GitHub 热门页展示图片、视频与语音生成服务开关', async () => {
  const page = await readFile(new URL('./components/AdminConsolePage.vue', import.meta.url), 'utf8')
  const styles = await readFile(new URL('./styles.css', import.meta.url), 'utf8')

  assert.match(page, /内容生成服务/)
  assert.match(page, /v-model="form\.image_generation_enabled"/)
  assert.match(page, /v-model="form\.video_generation_enabled"/)
  assert.match(page, /v-model="form\.audio_generation_enabled"/)
  assert.match(page, /不会调用图片生成服务/)
  assert.match(page, /不进入视频分镜、生成和装配任务链/)
  assert.match(page, /不会调用语音合成服务/)
  assert.match(styles, /\.admin-media-control-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3,/s)
  assert.match(styles, /@media \(max-width: 640px\)[\s\S]*\.admin-media-control\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/)
})
