import { createApp } from 'vue'
import CareerGreetingPreviewPage from './components/CareerGreetingPreviewPage.vue'
import './styles.css'
import './theme.css'

document.documentElement.dataset.uiTheme = 'blue'
createApp(CareerGreetingPreviewPage).mount('#app')
