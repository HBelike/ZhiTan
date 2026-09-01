import test from 'node:test'
import assert from 'node:assert/strict'

import { toTargetRolePayload } from './job-library-target-role.js'

test('将完整职位详情映射为现有目标岗位 payload', () => {
  const job = {
    title: ' 全栈开发工程师 ',
    company: '元响科技有限公司',
    companyShort: '元响科技',
    salary: '15-30K·14薪',
    city: '上海',
    district: '杨浦区',
    experience: '经验不限',
    degree: '本科',
    description: '负责全栈产品研发。\n熟练使用 Vue 和 FastAPI。',
    skills: ['Vue', 'FastAPI'],
    welfare: ['五险一金', '带薪年假'],
    address: '上海市杨浦区五角场',
    sourceUrl: 'https://www.zhipin.com/job_detail/example.html'
  }
  const original = structuredClone(job)

  const payload = toTargetRolePayload(job)

  assert.equal(payload.company_name, '元响科技有限公司')
  assert.equal(payload.role_name, '全栈开发工程师')
  assert.equal(payload.source_kind, 'job_library')
  assert.equal(payload.source_label, job.sourceUrl)
  assert.match(payload.job_text, /^# 全栈开发工程师/m)
  assert.match(payload.job_text, /公司：元响科技有限公司/)
  assert.match(payload.job_text, /地点：上海 · 杨浦区/)
  assert.match(payload.job_text, /## 职位描述\n负责全栈产品研发。/)
  assert.match(payload.job_text, /## 技能关键词\nVue、FastAPI/)
  assert.match(payload.job_text, /## 工作地点\n上海市杨浦区五角场/)
  assert.match(payload.job_text, /## 职位福利\n五险一金、带薪年假/)
  assert.deepEqual(job, original)
})

test('缺省可选字段时不生成空章节', () => {
  const payload = toTargetRolePayload({
    title: '后端开发工程师',
    companyShort: '示例科技',
    description: '负责 API 与数据库开发。'
  })

  assert.equal(payload.company_name, '示例科技')
  assert.equal(payload.source_label, 'BOSS 直聘职位库')
  assert.doesNotMatch(payload.job_text, /薪资：/)
  assert.doesNotMatch(payload.job_text, /## 技能关键词/)
  assert.doesNotMatch(payload.job_text, /## 工作地点/)
  assert.doesNotMatch(payload.job_text, /## 职位福利/)
})

test('缺少完整职位正文时拒绝生成目标岗位', () => {
  assert.throws(
    () => toTargetRolePayload({ title: '后端开发', company: '示例科技', description: '' }),
    /缺少完整职位详情/
  )
})
