import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { AuthenticatedWorkspace } from './AuthenticatedWorkspace'
import { safeRedirect } from './redirect'
import { LoginPage } from '../pages/LoginPage'
import { AppShell } from './AppShell'
import { HomePage } from '../pages/HomePage'
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

const uiKitRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/ui-kit',
  component: UIKitPage,
})

const routeTree = rootRoute.addChildren([loginRoute, uiKitRoute, protectedRoute.addChildren([homeRoute])])

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
