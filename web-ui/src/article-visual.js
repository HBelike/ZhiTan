const FIGURE_ROLE_LABELS = Object.freeze({
  summary_card: '总结卡',
  flow: '步骤流程',
  architecture: '模块关系',
  comparison: '方案对比',
  timeline: '演进时间线'
})

const VALIDATION_LABELS = Object.freeze({
  passed: '校验通过',
  failed: '校验失败',
  pending: '等待校验',
  running: '正在校验'
})

function normalizedText(value) {
  return typeof value === 'string' ? value.trim() : ''
}

export function figureRoleLabel(role) {
  return FIGURE_ROLE_LABELS[normalizedText(role)] ?? '图型未知'
}

export function visualSpecSummary(spec) {
  if (!spec || typeof spec !== 'object') return '暂无图片总结方案'
  const headline = normalizedText(spec.headline)
  const purpose = normalizedText(spec.purpose)
  return [headline, purpose].filter(Boolean).join(' · ') || '暂无图片总结方案'
}

export function visualValidationLabel(result) {
  if (!result || typeof result !== 'object') return '未提供校验结果'
  return VALIDATION_LABELS[normalizedText(result.status)] ?? '校验状态未知'
}
