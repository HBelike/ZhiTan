import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  BOSS_EXTENSION_DOWNLOAD_URL,
  BOSS_EXTENSION_GUIDE_URL,
  BOSS_EXTENSION_REQUIRED_CAPABILITIES,
  BOSS_EXTENSION_VERSION,
  bossExtensionGuideUrl,
  normalizeBossExtensionConnection,
  shouldOpenBossExtensionGate
} from './boss-extension-onboarding.js'

const manifestUrl = new URL('../../browser-extension/job-library/manifest.json', import.meta.url)

test('扩展下载地址与真实 manifest 版本保持一致', async () => {
  const manifest = JSON.parse(await readFile(manifestUrl, 'utf8'))
  assert.equal(BOSS_EXTENSION_VERSION, manifest.version)
  assert.equal(
    BOSS_EXTENSION_DOWNLOAD_URL,
    `/downloads/find-job-boss-helper-v${manifest.version}.zip`
  )
})

test('已安装旧版助手保持已连接，只单独标记安全重试能力不足', () => {
  assert.deepEqual(normalizeBossExtensionConnection({ connected: true, version: '0.2.3' }), {
    status: 'ready',
    version: '0.2.3',
    capabilities: [],
    greetingRetryReady: false,
    greetingDispatchReady: false,
    missingCapabilities: BOSS_EXTENSION_REQUIRED_CAPABILITIES
  })
  assert.deepEqual(normalizeBossExtensionConnection({
    connected: true,
    version: '0.2.4',
    capabilities: [...BOSS_EXTENSION_REQUIRED_CAPABILITIES]
  }), {
    status: 'ready',
    version: '0.2.4',
    capabilities: BOSS_EXTENSION_REQUIRED_CAPABILITIES,
    greetingRetryReady: true,
    greetingDispatchReady: true,
    missingCapabilities: []
  })
  assert.deepEqual(normalizeBossExtensionConnection(null), {
    status: 'missing',
    version: '',
    capabilities: [],
    greetingRetryReady: false,
    greetingDispatchReady: false,
    missingCapabilities: BOSS_EXTENSION_REQUIRED_CAPABILITIES
  })
})

test('静默检测不弹安装窗口，只有用户操作且真正未连接时才弹出', () => {
  assert.equal(shouldOpenBossExtensionGate('missing'), false)
  assert.equal(shouldOpenBossExtensionGate('missing', { interactive: true }), true)
  assert.equal(shouldOpenBossExtensionGate('ready', { interactive: true }), false)
})

test('Chrome 与 Edge 使用同一教程页的独立步骤锚点', () => {
  assert.equal(BOSS_EXTENSION_GUIDE_URL, '/boss-extension-guide.html')
  assert.equal(bossExtensionGuideUrl('chrome'), '/boss-extension-guide.html#chrome')
  assert.equal(bossExtensionGuideUrl('edge'), '/boss-extension-guide.html#edge')
})
