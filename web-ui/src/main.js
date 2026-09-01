import { createApp } from 'vue'
import App from './App.vue'
import './styles.css'
import './theme.css'

const storedTheme = window.localStorage.getItem('find-job-ui-theme')
document.documentElement.dataset.uiTheme = storedTheme === 'green' ? 'green' : 'blue'

createApp(App).mount('#app')
