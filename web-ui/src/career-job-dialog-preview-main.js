import { createApp, h } from 'vue'
import CareerJobSearchDialog from './components/CareerJobSearchDialog.vue'
import './styles.css'
import './theme.css'

const WEB_CHANNEL = 'find-job-job-library-web-v1'
const EXTENSION_CHANNEL = 'find-job-job-library-extension-v1'

window.addEventListener('message', (event) => {
  const message = event.data
  if (event.source !== window || message?.channel !== WEB_CHANNEL) return

  const data = message.action === 'ping'
    ? {
        connected: true,
        version: '0.2.4',
        capabilities: ['retry_greeting_message', 'greeting_submission_state']
      }
    : {}

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

createApp({
  render() {
    return h(CareerJobSearchDialog, { open: true })
  }
}).mount('#app')
