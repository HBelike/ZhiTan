export const DEFAULT_APP_ROUTE = '/career'

const AUTH_ROUTES = new Set(['/login', '/register', '/forgot-password'])
const ADMIN_ROUTES = new Set(['/admin/modules', '/admin/github', '/admin/prompts', '/admin/model-context'])

export function normalizeAppRoute(pathname, reviewRoutes = []) {
  if (!pathname || pathname === '/') return DEFAULT_APP_ROUTE
  if (AUTH_ROUTES.has(pathname)) return pathname
  if (pathname === '/career') return '/career'
  if (pathname === '/career/interview-master') return '/career/interview-master'
  if (pathname === '/career/online-assessment') return '/career/online-assessment'
  if (pathname === '/interviews/jobs') return '/interviews/jobs'
  if (pathname === '/interviews') return '/interviews'
  if (pathname === '/skills' || pathname.startsWith('/skills/')) return '/skills'
  if (pathname === '/observability') return '/observability'
  if (pathname === '/admin') return '/admin/modules'
  if (ADMIN_ROUTES.has(pathname)) return pathname
  if (pathname.startsWith('/admin/')) return '/admin/modules'
  if (pathname.startsWith('/review')) {
    return reviewRoutes.includes(pathname) ? pathname : '/review'
  }
  return DEFAULT_APP_ROUTE
}

export function resolveAuthenticatedRoute(route) {
  return AUTH_ROUTES.has(route) ? DEFAULT_APP_ROUTE : route
}

export function canAccessNavigationItem(item, configuredModule, role) {
  if (role === 'admin') return true
  if (!item?.enabled) return false
  if (item.requiredRole && role !== item.requiredRole) return false
  return Boolean(configuredModule?.accessible)
}

export function canAccessConfiguredFeature(configuredFeature, _role) {
  return Boolean(configuredFeature?.enabled && configuredFeature?.accessible)
}

export function buildNavigationFallback(items, role) {
  const isAdmin = role === 'admin'
  return items.map((item) => ({
    key: item.moduleKey,
    enabled: isAdmin,
    accessible: isAdmin
  }))
}
