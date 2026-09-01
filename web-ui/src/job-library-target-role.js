const TARGET_ROLE_TEXT_LIMIT = 30000

function cleanText(value) {
  return String(value ?? '').trim()
}

function cleanList(value) {
  if (!Array.isArray(value)) return []
  return [...new Set(value.map(cleanText).filter(Boolean))]
}

function locationText(job) {
  return [...new Set([cleanText(job?.city), cleanText(job?.district)].filter(Boolean))].join(' · ')
}

function appendSection(parts, title, content) {
  const normalized = cleanText(content)
  if (!normalized) return
  parts.push(`## ${title}\n${normalized}`)
}

export function buildCanonicalJobText(job = {}) {
  const title = cleanText(job.title)
  const company = cleanText(job.company || job.companyShort)
  const parts = [`# ${title}`]
  const metadata = [
    company ? `公司：${company}` : '',
    cleanText(job.salary) ? `薪资：${cleanText(job.salary)}` : '',
    locationText(job) ? `地点：${locationText(job)}` : '',
    cleanText(job.experience) ? `经验：${cleanText(job.experience)}` : '',
    cleanText(job.degree) ? `学历：${cleanText(job.degree)}` : ''
  ].filter(Boolean)

  if (metadata.length) parts.push(metadata.join('\n'))
  appendSection(parts, '职位描述', job.description)
  appendSection(parts, '技能关键词', cleanList(job.skills).join('、'))
  appendSection(parts, '工作地点', job.address)
  appendSection(parts, '职位福利', cleanList(job.welfare).join('、'))
  return parts.join('\n\n').slice(0, TARGET_ROLE_TEXT_LIMIT)
}

export function toTargetRolePayload(job) {
  const title = cleanText(job?.title)
  const company = cleanText(job?.company || job?.companyShort)
  const description = cleanText(job?.description)
  if (!title || !company || !description) {
    throw new Error('所选岗位缺少完整职位详情，请重新获取后再确认')
  }

  return {
    company_name: company,
    role_name: title,
    source_kind: 'job_library',
    source_label: cleanText(job?.sourceUrl) || 'BOSS 直聘职位库',
    job_text: buildCanonicalJobText(job)
  }
}
