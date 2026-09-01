import assert from 'node:assert/strict'
import test from 'node:test'

import {
  figureRoleLabel,
  visualSpecSummary,
  visualValidationLabel
} from './article-visual.js'

test('图片方案使用中文图型和确定性校验状态', () => {
  assert.equal(figureRoleLabel('summary_card'), '总结卡')
  assert.equal(figureRoleLabel('flow'), '步骤流程')
  assert.equal(figureRoleLabel('architecture'), '模块关系')
  assert.equal(figureRoleLabel('comparison'), '方案对比')
  assert.equal(figureRoleLabel('timeline'), '演进时间线')
  assert.equal(figureRoleLabel('unknown'), '图型未知')

  assert.equal(visualValidationLabel({ status: 'passed' }), '校验通过')
  assert.equal(visualValidationLabel({ status: 'failed' }), '校验失败')
  assert.equal(visualValidationLabel({ status: 'pending' }), '等待校验')
  assert.equal(visualValidationLabel({ status: 'running' }), '正在校验')
  assert.equal(visualValidationLabel({ status: 'unexpected' }), '校验状态未知')
  assert.equal(visualValidationLabel(null), '未提供校验结果')

  assert.equal(
    visualSpecSummary({ headline: '从规格到任务', purpose: '解释交付链路' }),
    '从规格到任务 · 解释交付链路'
  )
  assert.equal(visualSpecSummary({ headline: '从规格到任务' }), '从规格到任务')
  assert.equal(visualSpecSummary({ purpose: '解释交付链路' }), '解释交付链路')
  assert.equal(visualSpecSummary(null), '暂无图片总结方案')
})
