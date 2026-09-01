function stringValue(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function stringList(value) {
  if (!Array.isArray(value)) return []
  return value.map(stringValue).filter(Boolean)
}

function joinLocation(...parts) {
  return parts.map(stringValue).filter(Boolean).join(' · ')
}

function cityValue(value, fallbackInitial = '#') {
  const code = String(value?.code ?? '').trim()
  const name = stringValue(value?.name)
  if (!code || !name) return null
  const rawInitial = stringValue(value?.firstChar).slice(0, 1).toUpperCase()
  return {
    code,
    name,
    initial: /^[A-Z]$/.test(rawInitial) ? rawInitial : fallbackInitial,
    pinyin: stringValue(value?.pinyin).toLowerCase()
  }
}

function sortCities(cities) {
  return cities.sort((left, right) => {
    if (left.initial === '#' && right.initial !== '#') return 1
    if (right.initial === '#' && left.initial !== '#') return -1
    return left.initial.localeCompare(right.initial) || left.name.localeCompare(right.name, 'zh-CN')
  })
}

function normalizeCityCode(value) {
  const requestedCity = stringValue(value)
  return /^\d{9}$/.test(requestedCity) ? requestedCity : '101020100'
}

function normalizeCityName(value) {
  return stringValue(value)
    .replace(/(?:特别行政区|壮族自治区|回族自治区|维吾尔自治区|自治区)$/u, '')
    .replace(/(?:自治州|地区|盟|市)$/u, '')
}

export function buildSearchEndpoint(query, page = 1, city = '101020100') {
  const normalizedQuery = stringValue(query).slice(0, 80)
  if (!normalizedQuery) throw new Error('岗位名称不能为空。')
  const normalizedPage = Math.max(1, Math.min(20, Number(page) || 1))
  const normalizedCity = normalizeCityCode(city)
  const params = new URLSearchParams({
    scene: '1',
    query: normalizedQuery,
    city: normalizedCity,
    page: String(normalizedPage),
    pageSize: '15',
    experience: '',
    degree: '',
    salary: '',
    scale: '',
    industry: '',
    jobType: ''
  })
  return `/wapi/zpgeek/search/joblist.json?${params.toString()}`
}

export function buildCityEndpoint() {
  return '/wapi/zpgeek/common/data/city/site.json'
}

export function buildBossSearchPageUrl(query, city = '101020100') {
  const url = new URL('https://www.zhipin.com/web/geek/jobs')
  url.search = new URLSearchParams({
    query: stringValue(query).slice(0, 80),
    city: normalizeCityCode(city),
    ka: 'search_list'
  }).toString()
  return url.toString()
}

export function shouldSyncBossSearchPage(currentUrl, city = '101020100') {
  try {
    const url = new URL(currentUrl)
    return url.hostname !== 'www.zhipin.com'
      || url.pathname !== '/web/geek/jobs'
      || url.searchParams.get('city') !== normalizeCityCode(city)
  } catch {
    return true
  }
}

export function buildDetailEndpoint(securityId) {
  const normalizedSecurityId = stringValue(securityId).slice(0, 600)
  if (!normalizedSecurityId) throw new Error('岗位详情标识不能为空。')
  return `/wapi/zpgeek/job/detail.json?securityId=${encodeURIComponent(normalizedSecurityId)}`
}

export function normalizeCityResponse(response) {
  const payload = response?.zpData ?? {}
  const cityMap = new Map()
  for (const group of Array.isArray(payload.cityList) ? payload.cityList : []) {
    const children = Array.isArray(group?.subLevelModelList) ? group.subLevelModelList : []
    const records = children.length ? children : [group]
    for (const item of records) {
      const city = cityValue(item)
      if (city) cityMap.set(city.code, city)
    }
  }

  const hotCities = []
  for (const item of Array.isArray(payload.hotCityList) ? payload.hotCityList : []) {
    const hot = cityValue(item)
    if (!hot) continue
    const known = cityMap.get(hot.code)
    hot.initial = known?.initial ?? hot.initial
    hotCities.push(hot)
    cityMap.set(hot.code, known ?? hot)
  }

  return {
    hotCities,
    cities: sortCities([...cityMap.values()])
  }
}

export function normalizeSearchResponse(response, context = {}) {
  const payload = response?.zpData ?? {}
  const rawJobs = Array.isArray(payload.jobList) ? payload.jobList : []
  const requestedCityCode = stringValue(context.cityCode)
  const requestedCityName = normalizeCityName(context.cityName)
  const shouldEnforceCity = /^\d{9}$/.test(requestedCityCode)
    && requestedCityCode !== '100010000'
    && Boolean(requestedCityName)
  let rejectedCityCount = 0
  const jobs = rawJobs.map((job) => {
    const rawCity = stringValue(job.cityName)
    if (shouldEnforceCity && normalizeCityName(rawCity) !== requestedCityName) {
      rejectedCityCount += 1
      return null
    }
    const securityId = stringValue(job.securityId)
    const jobId = stringValue(job.encryptJobId)
    const skills = stringList(job.skills)
    const labels = stringList(job.jobLabels)
    return {
      id: securityId || jobId,
      securityId,
      jobId,
      bossId: stringValue(job.encryptBossId),
      lid: stringValue(job.lid),
      title: stringValue(job.jobName) || '未命名岗位',
      salary: stringValue(job.salaryDesc) || '薪资面议',
      experience: stringValue(job.jobExperience) || labels[0] || '经验不限',
      degree: stringValue(job.jobDegree) || labels[1] || '学历不限',
      city: rawCity,
      district: joinLocation(job.areaDistrict, job.businessDistrict),
      company: stringValue(job.brandName) || '公司信息待补充',
      companyShort: stringValue(job.brandName) || '公司',
      industry: stringValue(job.brandIndustry),
      scale: stringValue(job.brandScaleName),
      stage: stringValue(job.brandStageName),
      recruiter: joinLocation(job.bossName, job.bossTitle),
      recruiterOnline: Boolean(job.bossOnline),
      recruiterAvatar: stringValue(job.bossAvatar),
      companyLogo: stringValue(job.brandLogo),
      skills,
      labels,
      welfare: stringList(job.welfareList),
      sourceUrl: jobId ? `https://www.zhipin.com/job_detail/${encodeURIComponent(jobId)}.html` : '',
      fetchedAt: new Date().toISOString()
    }
  }).filter((job) => job?.id)

  return {
    query: stringValue(context.query),
    city: stringValue(context.cityName) || '上海',
    page: Number(context.page) || 1,
    hasMore: Boolean(payload.hasMore) && jobs.length > 0,
    rawJobCount: rawJobs.length,
    rejectedCityCount,
    jobs
  }
}

export function normalizeDetailResponse(response, fallback = {}) {
  const payload = response?.zpData ?? {}
  const job = payload.jobInfo ?? {}
  const boss = payload.bossInfo ?? {}
  const brand = payload.brandComInfo ?? {}
  const jobId = stringValue(job.encryptJobId) || stringValue(fallback.jobId)
  return {
    ...fallback,
    id: stringValue(fallback.id) || stringValue(fallback.securityId) || jobId,
    securityId: stringValue(fallback.securityId),
    jobId,
    bossId: stringValue(boss.encryptBossId) || stringValue(fallback.bossId),
    lid: stringValue(job.lid) || stringValue(fallback.lid),
    title: stringValue(job.jobName) || stringValue(fallback.title) || '未命名岗位',
    salary: stringValue(job.salaryDesc) || stringValue(fallback.salary) || '薪资面议',
    experience: stringValue(job.experienceName) || stringValue(fallback.experience) || '经验不限',
    degree: stringValue(job.degreeName) || stringValue(fallback.degree) || '学历不限',
    city: stringValue(job.locationName) || stringValue(fallback.city) || '上海',
    district: stringValue(fallback.district),
    address: stringValue(job.address),
    longitude: Number(job.longitude) || null,
    latitude: Number(job.latitude) || null,
    description: stringValue(job.postDescription),
    skills: stringList(job.showSkills).length ? stringList(job.showSkills) : stringList(fallback.skills),
    welfare: stringList(job.welfareList).length ? stringList(job.welfareList) : stringList(fallback.welfare),
    recruiter: joinLocation(boss.name, boss.title) || stringValue(fallback.recruiter),
    recruiterOnline: Boolean(boss.online ?? fallback.recruiterOnline),
    recruiterAvatar: stringValue(boss.large) || stringValue(fallback.recruiterAvatar),
    company: stringValue(brand.brandName) || stringValue(fallback.company) || '公司信息待补充',
    companyShort: stringValue(brand.brandName) || stringValue(fallback.companyShort) || '公司',
    companyLogo: stringValue(brand.logo) || stringValue(fallback.companyLogo),
    industry: stringValue(brand.industryName) || stringValue(fallback.industry),
    scale: stringValue(brand.scaleName) || stringValue(fallback.scale),
    stage: stringValue(brand.stageName) || stringValue(fallback.stage),
    sourceUrl: jobId
      ? `https://www.zhipin.com/job_detail/${encodeURIComponent(jobId)}.html`
      : stringValue(fallback.sourceUrl),
    fetchedAt: new Date().toISOString()
  }
}

export function classifyBossFailure(result) {
  if (!result) return { code: 'empty_response', message: 'BOSS 直聘没有返回数据。' }
  if (result.kind === 'verification') {
    return { code: 'verification_required', message: 'BOSS 直聘要求安全验证，请在已打开的 BOSS 页面完成验证后重试。' }
  }
  if (result.kind === 'login') {
    return { code: 'login_required', message: '请先在浏览器中登录 BOSS 直聘，然后回到职位库重试。' }
  }
  if (result.kind === 'network') {
    return { code: 'network_error', message: result.message || 'BOSS 直聘网络请求失败，请稍后重试。' }
  }
  if (result.kind === 'http') {
    const status = Number(result.status)
    if (status === 429) {
      return {
        code: 'rate_limited',
        message: 'BOSS 直聘限制了当前访问频率。为保护账号，助手不会自动刷新或重试，请稍后再试。'
      }
    }
    return {
      code: 'boss_http_error',
      message: `BOSS 直聘返回 HTTP ${status || '异常状态'}，助手不会自动重试。`
    }
  }
  const code = Number(result.response?.code)
  if (code === 37) {
    return {
      code: 'session_stale',
      message: 'BOSS 页面会话状态需要更新。',
      recoverableByRefresh: true
    }
  }
  if (code === 429) {
    return {
      code: 'rate_limited',
      message: 'BOSS 直聘限制了当前访问频率。为保护账号，助手不会自动刷新或重试，请稍后再试。'
    }
  }
  if (code !== 0) {
    return {
      code: 'boss_api_error',
      message: stringValue(result.response?.message) || stringValue(result.response?.msg) || 'BOSS 直聘暂时无法返回职位信息。'
    }
  }
  return null
}

export function shouldRefreshBossSession(failure, action = '') {
  const normalizedAction = stringValue(action)
  if (normalizedAction && !['search_jobs', 'get_job_detail'].includes(normalizedAction)) return false
  return failure?.code === 'session_stale' && failure?.recoverableByRefresh === true
}
