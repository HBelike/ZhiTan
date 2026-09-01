export const DEFAULT_JOB_CITY = Object.freeze({
  code: '101020100',
  name: '上海',
  initial: 'S',
  pinyin: 'shanghai'
})

export const FALLBACK_JOB_CITIES = Object.freeze([
  { code: '100010000', name: '全国', initial: '#', pinyin: 'quanguo' },
  { code: '101010100', name: '北京', initial: 'B', pinyin: 'beijing' },
  DEFAULT_JOB_CITY,
  { code: '101280100', name: '广州', initial: 'G', pinyin: 'guangzhou' },
  { code: '101280200', name: '深圳', initial: 'S', pinyin: 'shenzhen' },
  { code: '101210100', name: '杭州', initial: 'H', pinyin: 'hangzhou' },
  { code: '101030100', name: '天津', initial: 'T', pinyin: 'tianjin' },
  { code: '101110100', name: '西安', initial: 'X', pinyin: 'xian' },
  { code: '101190400', name: '苏州', initial: 'S', pinyin: 'suzhou' },
  { code: '101200100', name: '武汉', initial: 'W', pinyin: 'wuhan' },
  { code: '101230200', name: '厦门', initial: 'X', pinyin: 'xiamen' },
  { code: '101250100', name: '长沙', initial: 'C', pinyin: 'changsha' },
  { code: '101270100', name: '成都', initial: 'C', pinyin: 'chengdu' },
  { code: '101180100', name: '郑州', initial: 'Z', pinyin: 'zhengzhou' },
  { code: '101040100', name: '重庆', initial: 'C', pinyin: 'chongqing' }
])

function normalizeCity(value) {
  const code = String(value?.code ?? '').trim()
  const name = String(value?.name ?? '').trim()
  if (!code || !name) return null
  const rawInitial = String(value?.initial ?? '').trim().slice(0, 1).toUpperCase()
  return {
    code,
    name,
    initial: /^[A-Z]$/.test(rawInitial) ? rawInitial : '#',
    pinyin: String(value?.pinyin ?? '').trim().toLowerCase()
  }
}

function citySort(left, right) {
  if (left.initial === '#' && right.initial !== '#') return -1
  if (right.initial === '#' && left.initial !== '#') return 1
  return left.initial.localeCompare(right.initial) || left.name.localeCompare(right.name, 'zh-CN')
}

export function mergeJobCityCatalog(payload = {}) {
  const merged = new Map(FALLBACK_JOB_CITIES.map((city) => [city.code, { ...city }]))
  for (const source of Array.isArray(payload.cities) ? payload.cities : []) {
    const city = normalizeCity(source)
    if (city) merged.set(city.code, city)
  }
  return [...merged.values()].sort(citySort)
}

export function normalizeHotJobCities(payload = {}) {
  const source = Array.isArray(payload.hotCities) && payload.hotCities.length
    ? payload.hotCities
    : FALLBACK_JOB_CITIES
  return source.map(normalizeCity).filter(Boolean)
}

export function groupJobCities(cities) {
  const groups = new Map()
  for (const city of Array.isArray(cities) ? cities : []) {
    const normalized = normalizeCity(city)
    if (!normalized || normalized.initial === '#') continue
    if (!groups.has(normalized.initial)) groups.set(normalized.initial, [])
    groups.get(normalized.initial).push(normalized)
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([initial, items]) => ({
      initial,
      cities: items.sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'))
    }))
}

export function filterJobCities(cities, query) {
  const normalizedQuery = String(query ?? '').trim().toLowerCase().replace(/\s+/g, '')
  if (!normalizedQuery) return Array.isArray(cities) ? cities : []
  return (Array.isArray(cities) ? cities : []).filter((city) => {
    const name = String(city?.name ?? '').toLowerCase().replace(/\s+/g, '')
    const pinyin = String(city?.pinyin ?? '').toLowerCase().replace(/\s+/g, '')
    return name.includes(normalizedQuery) || pinyin.includes(normalizedQuery)
  })
}
