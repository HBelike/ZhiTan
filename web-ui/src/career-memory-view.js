export const CAREER_MEMORY_TYPES = [
  'job_intention',
  'work_experience',
  'education',
  'award',
  'publication',
  'personal_advantage'
]

export const CAREER_MEMORY_LABELS = {
  job_intention: '岗位意向',
  work_experience: '工作经历',
  education: '学历',
  award: '奖项',
  publication: '论文',
  personal_advantage: '个人优势'
}

export function groupCareerMemories(items = []) {
  return Object.fromEntries(CAREER_MEMORY_TYPES.map((type) => [type, {
    active: items.filter((item) => item.memory_type === type && item.status === 'active'),
    candidate: items.filter((item) => item.memory_type === type && item.status === 'candidate'),
    disabled: items.filter((item) => item.memory_type === type && item.status === 'disabled'),
    superseded: items.filter((item) => item.memory_type === type && item.status === 'superseded')
  }]))
}

export function memorySourceLabel(item = {}) {
  if (item.source_kind === 'confirmed_resume') {
    return `确认简历${item.candidate_profile_version ? ` v${item.candidate_profile_version}` : ''}`
  }
  if (item.source_kind === 'explicit_user_correction') return '用户修正'
  if (item.source_kind === 'user_confirmed_candidate') return '用户确认'
  return '对话中明确说明'
}
