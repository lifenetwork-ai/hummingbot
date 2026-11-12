import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { AuthenticatedWorkspace } from './AuthenticatedWorkspace'
import { safeRedirect } from './redirect'
import { LoginPage } from '../pages/LoginPage'
import { AppShell } from './AppShell'
import { HomePage } from '../pages/HomePage'
import { SessionPage } from '../pages/SessionPage'
import { OrdersPage } from '../pages/OrdersPage'
import { ActivityPage } from '../pages/ActivityPage'
import { DailyStatisticsPage } from '../pages/DailyStatisticsPage'
import { AdminPage } from '../pages/AdminPage'
import { UIKitPage } from '../pages/UIKitPage'
import { NotFoundPage } from '../pages/NotFoundPage'

const rootRoute = createRootRoute({ component: AppShell })

const protectedRoute = createRoute({ getParentRoute: () => rootRoute, id: 'protected', component: AuthenticatedWorkspace })
const loginRoute = createRoute({ getParentRoute: () => rootRoute, path: '/login', validateSearch: (search: Record<string, unknown>) => ({ redirect: safeRedirect(search.redirect) }), component: LoginPage })

const homeRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/',
  component: HomePage,
})

const sessionRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/session',
  component: SessionPage,
})

const ordersRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/orders',
  component: OrdersPage,
})

const activityRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/activity',
  component: ActivityPage,
})

const statisticsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/statistics',
  component: DailyStatisticsPage,
})

const adminRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/admin',
  component: AdminPage,
  validateSearch: (search: Record<string, unknown>): { tab?: 'connections' | 'recovery' | 'accounts' | 'audit' } => ({ tab: search.tab === 'connections' || search.tab === 'recovery' || search.tab === 'accounts' || search.tab === 'audit' ? search.tab : undefined }),
})

const uiKitRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/ui-kit',
  component: UIKitPage,
})

const routeTree = rootRoute.addChildren([
  loginRoute,
  uiKitRoute,
  protectedRoute.addChildren([
    homeRoute,
    sessionRoute,
    ordersRoute,
    activityRoute,
    statisticsRoute,
    adminRoute,
  ]),
])

export const router = createRouter({
  routeTree,
  defaultPreload: 'intent',
  defaultNotFoundComponent: NotFoundPage,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
