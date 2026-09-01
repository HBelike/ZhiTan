import { createRouter, createWebHashHistory } from 'vue-router'
import SetupPage from './pages/SetupPage.vue'
import SessionPage from './pages/SessionPage.vue'
import HistoryPage from './pages/HistoryPage.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/live-interview/setup' },
    { path: '/live-interview/setup', component: SetupPage },
    { path: '/live-interview/session/:id', component: SessionPage },
    { path: '/live-interview/history/:id', component: HistoryPage },
  ],
})
