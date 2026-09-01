const LEGACY_KEYS = ['skill_coverage', 'experience_coverage', 'project_relevance', 'critical_gap']
const SECTION_HEADING_ALIASES = new Set([
  '岗位职责', '职位职责', '工作职责', '工作内容', '核心职责', '主要职责', '你将负责',
  '任职要求', '岗位要求', '职位要求', '基本要求', '核心要求', '任职资格', '岗位资格', '任职条件', '我们希望', '你需要',
  '加分项', '优先条件', '优先考虑', '加分要求', '加分条件',
  '经验要求', '经验条件', '学历要求', '学历条件', '证书要求', '证书条件',
  '工作条件', '工作地点', '薪资福利', '职位福利', '福利待遇', '团队与福利', '我们提供', '你将获得',
  '公司介绍', '公司信息', '其他信息'
])

export function isAssessmentPending(assessment) {
  return ['queued', 'analyzing'].includes(assessment?.status)
}

export function buildAssessmentCards(assessment) {
  if (!assessment || !['ready', 'fallback_ready'].includes(assessment.status)) return []
  if (Array.isArray(assessment.dimensions)) {
    return assessment.dimensions
      .filter((item) => item?.status === 'ready' && item.score !== null)
      .map((item) => ({
        key: item.id,
        short: item.label,
        description: item.description,
        ...item
      }))
  }
  const dimensions = assessment.dimensions ?? {}
  return LEGACY_KEYS
    .filter((key) => dimensions[key]?.status === 'ready' && dimensions[key].score !== null)
    .map((key) => ({
      key,
      short: dimensions[key].label,
      description: legacyDescription(key),
      ...dimensions[key]
    }))
}

export function itemsForDimension(assessment, dimensionId) {
  if (!assessment) return []
  if (Array.isArray(assessment.items)) {
    if (dimensionId === 'critical_gap') {
      return assessment.items.filter((item) => item.requirement_type === 'required' && item.factor !== 1)
    }
    return assessment.items.filter((item) => item.dimension_id === dimensionId)
  }
  const requirements = assessment.requirements ?? []
  if (dimensionId === 'critical_gap') {
    return requirements.filter((item) => item.priority === 'must' && item.match_factor !== 1)
  }
  const category = {
    skill_coverage: 'skill',
    experience_coverage: 'experience',
    project_relevance: 'project'
  }[dimensionId]
  return requirements.filter((item) => (item.dimensions ?? [item.category]).includes(category))
}

export function sectionItemNumber(items, index) {
  return Array.isArray(items) && items.length > 1 ? index + 1 : null
}

export function assessmentCanvasSections(assessment) {
  if (assessment?.algorithm_version !== 'llm-judge-v1' || !Array.isArray(assessment.job_sections)) return null
  const sections = assessment.job_sections
  const items = (...categories) => sections
    .filter((section) => categories.includes(section.category))
    .flatMap((section) => cleanAssessmentSectionItems(section))
    .filter((item, index, values) => values.indexOf(item) === index)
  const supporting = [
    { key: 'work_condition', label: '工作条件', items: items('work_condition') },
    { key: 'compensation_benefit', label: '薪资福利', items: items('compensation_benefit') },
    { key: 'company_information', label: '公司信息', items: items('company_information') },
    { key: 'other', label: '其他信息', items: items('other') }
  ].filter((section) => section.items.length)
  return {
    responsibilities: items('responsibility'),
    requirements: [
      ...items('required_qualification', 'experience_condition', 'education_condition', 'credential_condition').map((text) => ({ text, type: '必须' })),
      ...items('preferred_qualification').map((text) => ({ text, type: '加分' }))
    ],
    supporting,
    hasStructure: sections.length > 0
  }
}

function cleanAssessmentSectionItems(section) {
  return (Array.isArray(section?.items) ? section.items : [])
    .map(cleanAssessmentSectionItem)
    .filter((item) => {
      const normalized = normalizedSectionHeading(item)
      return normalized && !SECTION_HEADING_ALIASES.has(normalized)
    })
}

function cleanAssessmentSectionItem(value) {
  return String(value ?? '')
    .replace(/<!--.*?-->/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/^\s{0,3}#{1,6}\s*/, '')
    .replace(/^\s*(?:\d+[.)、]|[一二三四五六七八九十]+[.)、])\s*/, '')
    .replace(/^\s*(?:[-*+•·▪◦—–])\s+/, '')
    .replace(/\*\*|__|`/g, '')
    .trim()
}

function normalizedSectionHeading(value) {
  return cleanAssessmentSectionItem(value)
    .replace(/^【|】$/g, '')
    .replace(/[：:]$/, '')
    .trim()
}

function legacyDescription(key) {
  return {
    skill_coverage: '简历证据覆盖了多少项岗位技能要求。',
    experience_coverage: '工作年限、教育背景等经验条件的达成情况。',
    project_relevance: '过往项目与岗位业务场景的关联程度。',
    critical_gap: '必须项中仍缺少明确简历证据的比例。'
  }[key] ?? '查看该项分析所依据的岗位要求和简历证据。'
}
