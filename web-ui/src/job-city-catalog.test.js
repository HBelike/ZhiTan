import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DEFAULT_JOB_CITY,
  FALLBACK_JOB_CITIES,
  filterJobCities,
  groupJobCities,
  mergeJobCityCatalog
} from './job-city-catalog.js'

test('默认城市保持上海，降级目录包含全国和主要热门城市', () => {
  assert.deepEqual(DEFAULT_JOB_CITY, { code: '101020100', name: '上海', initial: 'S', pinyin: 'shanghai' })
  assert.equal(FALLBACK_JOB_CITIES[0].name, '全国')
  assert.ok(FALLBACK_JOB_CITIES.some((city) => city.name === '深圳' && city.code === '101280200'))
})

test('动态城市目录覆盖降级项并按首字母分组', () => {
  const cities = mergeJobCityCatalog({
    cities: [
      { code: '101280100', name: '广州', initial: 'G', pinyin: 'guangzhou' },
      { code: '101020100', name: '上海', initial: 'S', pinyin: 'shanghai' }
    ]
  })
  const groups = groupJobCities(cities)

  assert.equal(cities.find((city) => city.name === '广州').code, '101280100')
  assert.ok(groups.some((group) => group.initial === 'G' && group.cities[0].name === '广州'))
})

test('城市搜索同时匹配中文名和拼音', () => {
  assert.deepEqual(filterJobCities(FALLBACK_JOB_CITIES, '深').map((city) => city.name), ['深圳'])
  assert.deepEqual(filterJobCities(FALLBACK_JOB_CITIES, 'hangzhou').map((city) => city.name), ['杭州'])
})
