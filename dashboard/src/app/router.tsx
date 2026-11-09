import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { AppShell } from './AppShell'
import { HomePage } from '../pages/HomePage'
import { UIKitPage } from '../pages/UIKitPage'
import { NotFoundPage } from '../pages/NotFoundPage'

const rootRoute = createRootRoute({ component: AppShell })

const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: HomePage,
})

const uiKitRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/ui-kit',
  component: UIKitPage,
})

const routeTree = rootRoute.addChildren([homeRoute, uiKitRoute])

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
