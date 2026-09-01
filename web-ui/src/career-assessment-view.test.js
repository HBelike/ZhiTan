import test from 'node:test'
import assert from 'node:assert/strict'
import {
  assessmentCanvasSections,
  buildAssessmentCards,
  isAssessmentPending,
  itemsForDimension,
  sectionItemNumber
} from './career-assessment-view.js'

const assessment = {
  status: 'ready',
  algorithm_version: 'llm-judge-v1',
  dimensions: [
    { id: 'performance', label: '才艺与表达', description: '考察演唱和表达', status: 'ready', score: 75, numerator: 1.5, denominator: 2 },
    { id: 'interaction', label: '互动能力', description: '考察直播互动', status: 'insufficient_data', score: null },
    { id: 'critical_gap', label: '关键要求缺口率', description: '硬性要求缺口', status: 'ready', score: 25, numerator: 0.5, denominator: 2 }
  ],
  items: [
    { id: 'one', dimension_id: 'performance', requirement_type: 'required', factor: 0.5 },
    { id: 'two', dimension_id: 'interaction', requirement_type: 'preferred', factor: null }
  ],
  job_sections: [
    { category: 'responsibility', items: ['唱歌和聊天互动'] },
    { category: 'required_qualification', items: ['具备稳定表达能力'] },
    { category: 'company_information', items: ['公司拥有十年行业经验'] }
  ]
}

test('动态指标不再出现技术岗位固定模板', () => {
  assert.deepEqual(buildAssessmentCards(assessment).map((item) => item.short), ['才艺与表达', '关键要求缺口率'])
})

test('基础规则指标隐藏不可计算项但保留零分关键缺口率', () => {
  const fallbackAssessment = {
    status: 'fallback_ready',
    algorithm_version: 'lexical-evidence-v2',
    dimensions: {
      skill_coverage: { label: '技能证据覆盖率', status: 'ready', score: 90.9 },
      experience_coverage: { label: '经验要求达成率', status: 'insufficient_data', score: null },
      project_relevance: { label: '项目场景适配率', status: 'ready', score: 92.9 },
      critical_gap: { label: '关键要求缺口率', status: 'ready', score: 0 }
    }
  }

  assert.deepEqual(
    buildAssessmentCards(fallbackAssessment).map((item) => [item.key, item.score]),
    [
      ['skill_coverage', 90.9],
      ['project_relevance', 92.9],
      ['critical_gap', 0]
    ]
  )
})

test('关键缺口只展示尚未充分满足的硬性要求', () => {
  assert.deepEqual(itemsForDimension(assessment, 'critical_gap').map((item) => item.id), ['one'])
})

test('只有运行中状态需要轮询', () => {
  assert.equal(isAssessmentPending({ status: 'queued' }), true)
  assert.equal(isAssessmentPending({ status: 'analyzing' }), true)
  assert.equal(isAssessmentPending({ status: 'ready' }), false)
})

test('Judge 分区把公司历史留在公司信息而不是任职要求', () => {
  const sections = assessmentCanvasSections(assessment)
  assert.deepEqual(sections.responsibilities, ['唱歌和聊天互动'])
  assert.deepEqual(sections.requirements, [{ text: '具备稳定表达能力', type: '必须' }])
  assert.deepEqual(sections.supporting[0], {
    key: 'company_information',
    label: '公司信息',
    items: ['公司拥有十年行业经验']
  })
})

test('Judge 分区过滤误入的标题并清理原文列表序号', () => {
  const sections = assessmentCanvasSections({
    algorithm_version: 'llm-judge-v1',
    job_sections: [
      {
        category: 'responsibility',
        title: '岗位职责',
        items: ['岗位职责', '1. 参与核心产品持续开发']
      },
      {
        category: 'required_qualification',
        title: '任职要求',
        items: ['任职要求', '1、4 年以上研发经验']
      },
      {
        category: 'compensation_benefit',
        title: '薪资福利',
        items: ['20-30K', '## 职位福利', '• 节日福利、带薪年假']
      }
    ]
  })

  assert.deepEqual(sections.responsibilities, ['参与核心产品持续开发'])
  assert.deepEqual(sections.requirements, [{ text: '4 年以上研发经验', type: '必须' }])
  assert.deepEqual(sections.supporting, [{
    key: 'compensation_benefit',
    label: '薪资福利',
    items: ['20-30K', '节日福利、带薪年假']
  }])
})

test('分区仅在存在多条有效记录时生成序号', () => {
  assert.equal(sectionItemNumber(['唯一记录'], 0), null)
  assert.equal(sectionItemNumber(['第一条', '第二条'], 0), 1)
  assert.equal(sectionItemNumber(['第一条', '第二条'], 1), 2)
})

test('与模型分区标题相同的真实正文不会被误删', () => {
  const sections = assessmentCanvasSections({
    algorithm_version: 'llm-judge-v1',
    job_sections: [{
      category: 'responsibility',
      title: '参与核心产品持续开发',
      items: ['参与核心产品持续开发']
    }]
  })

  assert.deepEqual(sections.responsibilities, ['参与核心产品持续开发'])
})
