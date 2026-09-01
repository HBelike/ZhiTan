import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildCityEndpoint,
  buildBossSearchPageUrl,
  buildDetailEndpoint,
  buildSearchEndpoint,
  classifyBossFailure,
  normalizeDetailResponse,
  normalizeCityResponse,
  normalizeSearchResponse,
  shouldSyncBossSearchPage,
  shouldRefreshBossSession
} from '../boss-data.js'

test('endpoint builders encode岗位名称并限制页码', () => {
  const search = buildSearchEndpoint('Agent 软件开发', 99, '101010100')
  assert.match(search, /^\/wapi\/zpgeek\/search\/joblist\.json\?/)
  assert.equal(new URL(`https://www.zhipin.com${search}`).searchParams.get('query'), 'Agent 软件开发')
  assert.equal(new URL(`https://www.zhipin.com${search}`).searchParams.get('page'), '20')
  assert.equal(new URL(`https://www.zhipin.com${search}`).searchParams.get('city'), '101010100')
  assert.equal(buildCityEndpoint(), '/wapi/zpgeek/common/data/city/site.json')
  const pageUrl = new URL(buildBossSearchPageUrl('后端 开发', '101010100'))
  assert.equal(pageUrl.pathname, '/web/geek/jobs')
  assert.equal(pageUrl.searchParams.get('query'), '后端 开发')
  assert.equal(pageUrl.searchParams.get('city'), '101010100')
  assert.equal(shouldSyncBossSearchPage(pageUrl.toString(), '101010100'), false)
  assert.equal(shouldSyncBossSearchPage(pageUrl.toString(), '101020100'), true)
  assert.equal(buildDetailEndpoint('secure/1'), '/wapi/zpgeek/job/detail.json?securityId=secure%2F1')
})

test('normalizeCityResponse maps热门城市和按字母分组的完整城市', () => {
  const result = normalizeCityResponse({
    code: 0,
    zpData: {
      hotCityList: [
        { code: 100010000, name: '全国' },
        { code: 101020100, name: '上海' }
      ],
      cityList: [
        {
          name: '上海',
          subLevelModelList: [
            { code: 101020100, name: '上海', firstChar: 's', pinyin: 'shanghai' }
          ]
        },
        {
          name: '广东',
          subLevelModelList: [
            { code: 101280100, name: '广州', firstChar: 'g', pinyin: 'guangzhou' },
            { code: 101280200, name: '深圳', firstChar: 's', pinyin: 'shenzhen' }
          ]
        }
      ]
    }
  })

  assert.deepEqual(result.hotCities, [
    { code: '100010000', name: '全国', initial: '#', pinyin: '' },
    { code: '101020100', name: '上海', initial: 'S', pinyin: '' }
  ])
  assert.deepEqual(result.cities.map((city) => city.name), ['广州', '上海', '深圳', '全国'])
  assert.equal(result.cities.find((city) => city.name === '广州').initial, 'G')
})

test('normalizeSearchResponse maps真实列表字段并保留详情标识', () => {
  const result = normalizeSearchResponse({
    code: 0,
    zpData: {
      hasMore: true,
      jobList: [{
        securityId: 'secure-1',
        encryptJobId: 'job-1',
        encryptBossId: 'boss-1',
        lid: 'lid-1',
        jobName: 'Agent开发工程师',
        salaryDesc: '30-50K',
        jobExperience: '3-5年',
        jobDegree: '本科',
        cityName: '上海',
        areaDistrict: '徐汇区',
        businessDistrict: '漕河泾',
        brandName: '示例科技',
        skills: ['Python', 'Agent']
      }]
    }
  }, { query: 'Agent', cityName: '上海', page: 1 })

  assert.equal(result.hasMore, true)
  assert.equal(result.jobs[0].id, 'secure-1')
  assert.equal(result.jobs[0].bossId, 'boss-1')
  assert.equal(result.jobs[0].lid, 'lid-1')
  assert.equal(result.jobs[0].district, '徐汇区 · 漕河泾')
  assert.equal(result.jobs[0].sourceUrl, 'https://www.zhipin.com/job_detail/job-1.html')
})

test('normalizeSearchResponse rejects jobs outside the requested city', () => {
  const result = normalizeSearchResponse({
    code: 0,
    zpData: {
      hasMore: true,
      jobList: [
        { securityId: 'beijing-1', jobName: '北京后端', cityName: '北京', brandName: '北京公司' },
        { securityId: 'shanghai-1', jobName: '上海后端', cityName: '上海', brandName: '上海公司' },
        { securityId: 'unknown-1', jobName: '未知地点后端', cityName: '', brandName: '未知公司' }
      ]
    }
  }, { query: '后端', cityCode: '101010100', cityName: '北京', page: 1 })

  assert.deepEqual(result.jobs.map((job) => job.id), ['beijing-1'])
  assert.equal(result.rejectedCityCount, 2)
  assert.equal(result.rawJobCount, 3)
})

test('normalizeDetailResponse maps职位正文、公司和招聘者字段', () => {
  const result = normalizeDetailResponse({
    code: 0,
    zpData: {
      jobInfo: {
        jobName: 'Agent平台工程师',
        salaryDesc: '40-60K',
        experienceName: '3-5年',
        degreeName: '本科',
        locationName: '上海',
        address: '上海市徐汇区',
        postDescription: '岗位职责\n1. 开发 Agent 平台',
        lid: 'detail-lid',
        showSkills: ['Java', 'LLM']
      },
      bossInfo: { name: '陈女士', title: '招聘经理', online: true, encryptBossId: 'detail-boss' },
      brandComInfo: { brandName: '示例科技', industryName: '人工智能', scaleName: '100-499人', stageName: 'B轮' }
    }
  }, { id: 'secure-1', securityId: 'secure-1' })

  assert.equal(result.description, '岗位职责\n1. 开发 Agent 平台')
  assert.equal(result.company, '示例科技')
  assert.equal(result.bossId, 'detail-boss')
  assert.equal(result.lid, 'detail-lid')
  assert.equal(result.recruiter, '陈女士 · 招聘经理')
  assert.deepEqual(result.skills, ['Java', 'LLM'])
})

test('classifyBossFailure distinguishes refreshable session state from real rate limits', () => {
  const verification = classifyBossFailure({ kind: 'verification' })
  const staleSession = classifyBossFailure({ kind: 'api', response: { code: 37 } })
  const rateLimited = classifyBossFailure({ kind: 'api', response: { code: 429 } })
  const httpRateLimited = classifyBossFailure({ kind: 'http', status: 429 })

  assert.equal(verification.code, 'verification_required')
  assert.equal(shouldRefreshBossSession(verification), false)
  assert.equal(staleSession.code, 'session_stale')
  assert.equal(shouldRefreshBossSession(staleSession), true)
  assert.equal(shouldRefreshBossSession(staleSession, 'search_jobs'), true)
  assert.equal(shouldRefreshBossSession(staleSession, 'get_job_detail'), true)
  assert.equal(shouldRefreshBossSession(staleSession, 'list_cities'), false)
  assert.equal(rateLimited.code, 'rate_limited')
  assert.equal(shouldRefreshBossSession(rateLimited), false)
  assert.equal(httpRateLimited.code, 'rate_limited')
  assert.equal(shouldRefreshBossSession(httpRateLimited), false)
  assert.equal(classifyBossFailure({ kind: 'api', response: { code: 0, zpData: {} } }), null)
})
