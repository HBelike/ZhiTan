import { createApp, h } from 'vue'
import CareerGreetingDialog from './components/CareerGreetingDialog.vue'
import './styles.css'
import './theme.css'

const WEB_CHANNEL = 'find-job-job-library-web-v1'
const EXTENSION_CHANNEL = 'find-job-job-library-extension-v1'
let firstGreetingAttempt = true
const legacyExtensionPreview = new URLSearchParams(window.location.search).get('legacy') === '1'

window.addEventListener('message', (event) => {
  const message = event.data
  if (event.source !== window || message?.channel !== WEB_CHANNEL) return

  if (message.action === 'ping') {
    window.postMessage({
      channel: EXTENSION_CHANNEL,
      requestId: message.requestId,
      ok: true,
      data: {
        connected: true,
        version: legacyExtensionPreview ? '0.2.3' : '0.2.7',
        capabilities: legacyExtensionPreview
          ? []
          : ['retry_greeting_message', 'greeting_submission_state', 'greeting_fire_and_continue']
      }
    }, window.location.origin)
    return
  }

  if (message.action === 'send_greeting' && firstGreetingAttempt) {
    firstGreetingAttempt = false
    window.postMessage({
      channel: EXTENSION_CHANNEL,
      requestId: message.requestId,
      ok: false,
      error: {
        code: 'verification_required',
        message: 'BOSS 要求安全验证，定制文案尚未发送。',
        stopBatch: true,
        retryable: true,
        retryMode: 'message',
        submissionState: 'not_submitted',
        defaultGreetingSent: true
      }
    }, window.location.origin)
    return
  }

  const data = message.action === 'preflight_greeting'
    ? { ready: true }
    : { status: 'sent', defaultGreetingSent: true, sentAt: new Date().toISOString() }
  window.postMessage({
    channel: EXTENSION_CHANNEL,
    requestId: message.requestId,
    ok: true,
    data
  }, window.location.origin)
})

document.documentElement.dataset.uiTheme = 'blue'
document.body.style.margin = '0'
document.body.style.minHeight = '100vh'
document.body.style.background = 'linear-gradient(135deg, #eef4fb, #dbe8f8)'

const previewJobs = [
  {
    id: 'retry-preview-1',
    securityId: 'security-1',
    jobId: 'job-1',
    bossId: 'boss-1',
    lid: 'lid-1',
    title: 'Java 全栈开发工程师',
    company: '瀚游科技',
    recruiter: '朱经理',
    description: '负责 Java 全栈系统交付。',
    skills: ['Java', 'Vue', '微服务']
  },
  {
    id: 'retry-preview-2',
    securityId: 'security-2',
    jobId: 'job-2',
    bossId: 'boss-2',
    lid: 'lid-2',
    title: '全栈开发工程师 · AI Agent',
    company: '苏州启行软件科技',
    recruiter: '李经理',
    description: '负责 AI Agent 与全栈产品开发。',
    skills: ['Agent', 'Java', 'Workflow']
  }
]

createApp({
  render() {
    return h(CareerGreetingDialog, {
      open: true,
      initialStage: 'review',
      candidateProfile: { version: 1 },
      previewJobs
    })
  }
}).mount('#app')
