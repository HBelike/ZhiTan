import { createApp, h } from 'vue'
import CareerAssistantPage from './components/CareerAssistantPage.vue'
import JobSearchWorkspace from './components/JobSearchWorkspace.vue'
import LoginPage from './components/LoginPage.vue'
import ResumeAssistantPage from './components/ResumeAssistantPage.vue'
import './styles.css'
import './theme.css'

const scenario = new URLSearchParams(window.location.search).get('scenario') || 'shell'
const WEB_CHANNEL = 'find-job-job-library-web-v1'
const EXTENSION_CHANNEL = 'find-job-job-library-extension-v1'

const previewJob = {
  id: 'job-1',
  securityId: 'security-1',
  jobId: 'boss-job-1',
  bossId: 'boss-1',
  lid: 'lid-1',
  title: 'Agent 软件开发工程师',
  salary: '25-40K',
  experience: '3-5年',
  degree: '本科',
  city: '北京',
  district: '海淀区',
  recruiter: '招聘负责人',
  recruiterOnline: true,
  company: '示例科技有限公司',
  companyShort: '示例科技',
  industry: '互联网',
  stage: 'B轮',
  scale: '100-499人',
  skills: ['Python', 'Agent', 'Vue'],
  welfare: ['五险一金', '弹性工作'],
  sourceUrl: 'https://www.zhipin.com/'
}

window.addEventListener('message', (event) => {
  const message = event.data
  if (event.source !== window || message?.channel !== WEB_CHANNEL) return

  let data = {}
  if (message.action === 'ping') {
    data = { connected: true, version: 'preview', capabilities: [] }
  } else if (message.action === 'list_cities') {
    data = { items: [] }
  } else if (message.action === 'search_jobs') {
    data = { jobs: [previewJob], hasMore: false }
  } else if (message.action === 'get_job_detail') {
    data = {
      ...previewJob,
      ...message.payload?.fallback,
      description: '负责 Agent 应用设计、工程实现与质量验证。\n与产品和后端协作交付稳定功能。',
      address: '北京市海淀区',
      fetchedAt: new Date('2026-08-27T10:00:00+08:00').toISOString()
    }
  }

  window.postMessage({
    channel: EXTENSION_CHANNEL,
    requestId: message.requestId,
    ok: true,
    data
  }, window.location.origin)
})

const navLabels = ['求职助手', '工作台', '简历助手', '面经库', '职位库']

function renderSidebar() {
  return h('aside', { id: 'app-navigation', class: 'sidebar' }, [
    h('div', { class: 'brand' }, [
      h('div', {
        class: 'brand-icon',
        'aria-hidden': 'true',
        innerHTML: '<svg viewBox="0 0 48 48" fill="none"><circle cx="13" cy="33" r="3.2" fill="currentColor"/><circle cx="24" cy="25" r="3.2" fill="currentColor"/><path d="M13 33c3.3-6.8 7.2-7.2 11-8 5-1.2 6.3-7.2 12-11" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"/><path d="m31.5 12.5 5 1-1 5" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M12 39h25" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity=".35"/></svg>'
      }),
      h('div', { class: 'brand-copy' }, [h('strong', '求职助手'), h('span', '让机会更近一步')])
    ]),
    h('nav', { class: 'nav-list', 'aria-label': '应用主导航' }, navLabels.map((label, index) => (
      h('button', { class: ['nav-item', index === 0 ? 'active' : ''], type: 'button' }, [
        h('span', { class: 'nav-icon' }, '◉'),
        h('span', { class: 'nav-copy' }, [h('strong', label)])
      ])
    )))
  ])
}

function renderTopbar(title) {
  return h('header', { class: 'topbar' }, [
    h('button', {
      class: 'mobile-nav-toggle',
      type: 'button',
      'aria-label': '打开导航'
    }, [h('span'), h('span'), h('span')]),
    h('h1', title)
  ])
}

function renderShellContent(title, content) {
  return h('div', { class: 'shell' }, [
    renderSidebar(),
    h('main', { class: ['main', title === '求职助手' ? 'career-route' : ''] }, [
      renderTopbar(title),
      content
    ])
  ])
}

const PreviewApp = {
  render() {
    if (scenario === 'login') return h(LoginPage)
    if (scenario === 'career') return renderShellContent('求职助手', h(CareerAssistantPage))
    if (scenario === 'job') return renderShellContent('职位库', h(JobSearchWorkspace))
    if (scenario === 'resume') {
      return renderShellContent('简历助手', h(ResumeAssistantPage, {
        currentUser: { display_name: '响应式验收用户' }
      }))
    }
    return renderShellContent('响应式壳层', h('section', { class: 'detail-card responsive-preview-card' }, [
      h('h2', '主内容区域'),
      h('p', '用于验证完整导航、紧凑导航和抽屉导航三档宽度。')
    ]))
  }
}

createApp(PreviewApp).mount('#app')
