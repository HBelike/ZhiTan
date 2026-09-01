import { createApp, h } from 'vue'
import BossExtensionInstallDialog from './components/BossExtensionInstallDialog.vue'
import './styles.css'
import './theme.css'

document.documentElement.dataset.uiTheme = 'blue'
document.body.style.margin = '0'
document.body.style.minHeight = '100vh'
document.body.style.background = 'linear-gradient(135deg, #eef4fb, #dbe8f8)'

createApp({
  render() {
    return h(BossExtensionInstallDialog, {
      open: true,
      error: '未检测到职位库浏览器助手，请先安装并启用扩展。'
    })
  }
}).mount('#app')
