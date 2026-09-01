import { test, expect } from '@playwright/test'
import { installApiFixtures } from './support/api-fixtures.js'

const previewPath = '/responsive-layout-preview.html'
const viewports = [
  { name: 'scaled-laptop', width: 900, height: 420 },
  { name: 'laptop-150', width: 911, height: 512 },
  { name: 'laptop-125', width: 1093, height: 614 },
  { name: 'compact-desktop', width: 1280, height: 720 },
  { name: 'desktop', width: 1440, height: 900 }
]

async function expectNoPageHorizontalOverflow(page) {
  await expect.poll(() => page.evaluate(() => (
    document.documentElement.scrollWidth <= document.documentElement.clientWidth
  ))).toBe(true)
}

test('应用壳层在目标 viewport 使用正确导航模式', async ({ page }) => {
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.goto(`${previewPath}?scenario=shell`)
    await expectNoPageHorizontalOverflow(page)

    const sidebar = page.locator('.sidebar')
    const toggle = page.locator('.mobile-nav-toggle')
    if (viewport.width >= 1280) {
      await expect(sidebar).toHaveCSS('width', '248px')
      await expect(toggle).toBeHidden()
    } else if (viewport.width >= 1024) {
      await expect(sidebar).toHaveCSS('width', '216px')
      await expect(toggle).toBeHidden()
    } else {
      await expect(toggle).toBeVisible()
      await expect(sidebar).toHaveCSS('position', 'fixed')
    }
  }
})

test('登录页在缩放笔记本下保留主任务并避免横向溢出', async ({ page }) => {
  await installApiFixtures(page)

  await page.setViewportSize({ width: 900, height: 420 })
  await page.goto(`${previewPath}?scenario=login`)
  await expect(page.getByRole('heading', { name: '登录求职助手' })).toBeVisible()
  await expect(page.locator('.login-career-panel')).toBeHidden()
  await expectNoPageHorizontalOverflow(page)

  await page.setViewportSize({ width: 1093, height: 614 })
  await expect(page.locator('.login-career-panel')).toBeVisible()
  await expect(page.locator('.login-submit')).toBeVisible()
  await expectNoPageHorizontalOverflow(page)
})

test('求职助手在紧凑容器中打开并用 Escape 关闭上下文抽屉', async ({ page }) => {
  await installApiFixtures(page)
  await page.setViewportSize({ width: 900, height: 420 })
  await page.goto(`${previewPath}?scenario=career`)

  await page.getByRole('button', { name: /^Agent 工程师求职准备/ }).click()
  const contextTrigger = page.getByRole('button', { name: '职位信息', exact: true })
  await expect(contextTrigger).toBeVisible()
  await contextTrigger.click()
  await expect(page.locator('.career-workspace')).toHaveClass(/compact-context-open/)
  await page.keyboard.press('Escape')
  await expect(page.locator('.career-workspace')).not.toHaveClass(/compact-context-open/)
  await expect(contextTrigger).toBeFocused()
  await expectNoPageHorizontalOverflow(page)
})

test('岗位检索窄容器保持列表与详情状态', async ({ page }) => {
  await page.setViewportSize({ width: 740, height: 620 })
  await page.goto(`${previewPath}?scenario=job`)

  await page.getByLabel('岗位名称').fill('Agent 工程师')
  await page.getByRole('button', { name: '搜索真实岗位' }).click()
  await page.getByRole('button', { name: /Agent 软件开发工程师/ }).click()
  await expect(page.locator('.job-browser')).toHaveClass(/show-compact-detail/)
  await expect(page.getByRole('button', { name: '返回岗位列表' })).toBeVisible()
  await page.getByRole('button', { name: '返回岗位列表' }).click()
  await expect(page.locator('.job-browser')).not.toHaveClass(/show-compact-detail/)
  await expect(page.getByLabel('岗位名称')).toHaveValue('Agent 工程师')
  await expectNoPageHorizontalOverflow(page)
})

test('简历助手在 900px viewport 使用保留状态的工作区标签', async ({ page }) => {
  await installApiFixtures(page)
  await page.setViewportSize({ width: 900, height: 620 })
  await page.goto(`${previewPath}?scenario=resume`)

  await page.getByRole('button', { name: /新建优化任务/ }).click()
  await page.locator('#resume-file-input').setInputFiles({
    name: 'resume.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.4 responsive test')
  })
  await page.getByLabel('岗位介绍').fill('负责 Agent 应用开发与质量保障')
  await page.getByRole('button', { name: '开始分析' }).click()

  const workspaceTabs = page.getByRole('tablist', { name: '简历工作区' })
  await expect(workspaceTabs).toBeVisible()
  await page.getByRole('tab', { name: '原始简历', exact: true }).click()
  await expect(page.locator('.resume-assistant')).toHaveClass(/show-workspace-document/)
  await page.getByRole('tab', { name: '优化助手' }).click()
  await expect(page.getByText('强化 Agent 项目结果')).toBeVisible()
  await expectNoPageHorizontalOverflow(page)
})
