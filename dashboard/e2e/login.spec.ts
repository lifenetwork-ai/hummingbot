import { test, expect } from '@playwright/test'
import { mockGoogle, signOut } from './auth-fixture'
import { AUTH_TOKEN_STORAGE_PREFIX, authTokenStorageKey } from '../src/core/auth/auth-persistence'

test.beforeEach(async ({ page }) => mockGoogle(page))

test('UI kit stays public across reload without authenticating', async ({ page }) => {
  const authRequests: string[] = []
  page.on('request', (request) => {
    if (/\/api\/v1\/admin\/access\/(login|me)/.test(request.url())) authRequests.push(request.url())
  })
  await page.goto('/ui-kit')
  await expect(page.getByRole('heading', { name: 'UI kit.' })).toBeVisible()
  await page.reload()
  await expect(page).toHaveURL('/ui-kit')
  await expect(page.getByRole('heading', { name: 'UI kit.' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign out' })).toHaveCount(0)
  expect(authRequests).toEqual([])
})

test('preserves deep links and restores sign-in after reload until explicit logout', async ({ page }) => {
  const destination = '/?market=spot#orders'
  await page.goto(destination)
  await expect(page).toHaveURL(/\/login\?redirect=/)
  await expect(page.getByRole('heading', { name: 'A clean canvas for what comes next.' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page).toHaveURL(destination)
  await signOut(page)
  await expect(page.getByRole('heading', { name: 'Đăng nhập' })).toBeVisible()
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page).toHaveURL(destination)
  await page.reload()
  await expect(page).toHaveURL(destination)
  await expect(page.getByRole('button', { name: /^Tài khoản / })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign in with Google' })).toHaveCount(0)
  expect(await page.evaluate((prefix) => Object.keys(localStorage).filter((key) => key.startsWith(prefix)).length, AUTH_TOKEN_STORAGE_PREFIX)).toBe(1)
  await signOut(page)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Đăng nhập' })).toBeVisible()
  expect(await page.evaluate((prefix) => Object.keys(localStorage).filter((key) => key.startsWith(prefix)), AUTH_TOKEN_STORAGE_PREFIX)).toEqual([])
})

test('denied login stays protected and can retry', async ({ page }) => {
  await page.route('**/api/v1/admin/access/login', (route) => route.fulfill({ status: 403, json: { code: 'account_not_allowed' } }))
  await page.goto('/')
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign out' })).toHaveCount(0)
  await page.unroute('**/api/v1/admin/access/login')
  await mockGoogle(page)
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page).toHaveURL('/')
})

for (const width of [320, 1280]) {
  test(`login supports keyboard and layout at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/login?redirect=https://outside.test')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const button = page.getByRole('button', { name: 'Sign in with Google' })
    await button.focus()
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL('/')
  })
}

test('new tab and reopening a tab restore through GET /me, and logout reaches the other tab', async ({ page, context }) => {
  let loginPosts = 0
  context.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/access/login')) loginPosts++
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page.getByRole('button', { name: /^Tài khoản / })).toBeVisible()
  const second = await context.newPage()
  await mockGoogle(second)
  await second.goto('/?market=spot#orders')
  await expect(second.getByRole('button', { name: /^Tài khoản / })).toBeVisible()
  await expect(second).toHaveURL('/?market=spot#orders')
  await second.close()
  const reopened = await context.newPage()
  await mockGoogle(reopened)
  await reopened.goto('/')
  await expect(reopened.getByRole('button', { name: /^Tài khoản / })).toBeVisible()
  expect(loginPosts).toBe(1)
  await signOut(page)
  await expect(reopened.getByRole('heading', { name: 'Đăng nhập' })).toBeVisible()
  await reopened.reload()
  await expect(reopened.getByRole('heading', { name: 'Đăng nhập' })).toBeVisible()
})

test('an expired stored token requires sign-in and is not sent to /me', async ({ page }) => {
  const key = authTokenStorageKey('/api/v1/admin', 'browser-test.apps.googleusercontent.com')
  await page.addInitScript(({ key }) => {
    localStorage.setItem(key, `header.${btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) - 1 }))}.signature`)
  }, { key })
  let identityReads = 0
  page.on('request', (request) => { if (request.url().includes('/access/me')) identityReads++ })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Đăng nhập' })).toBeVisible()
  expect(identityReads).toBe(0)
  expect(await page.evaluate((key) => localStorage.getItem(key), key)).toBeNull()
})

test('a stored token is verified before redirect, Google button or operational queries mount', async ({ page }) => {
  const key = authTokenStorageKey('/api/v1/admin', 'browser-test.apps.googleusercontent.com')
  await page.addInitScript(({ key }) => {
    localStorage.setItem(key, `header.${btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 600 }))}.signature`)
  }, { key })
  let releaseIdentity: (() => void) | undefined
  let operationalReads = 0
  let loginPosts = 0
  page.on('request', (request) => {
    if (/\/api\/v1\/admin\/(status|balances)/.test(request.url())) operationalReads++
    if (request.method() === 'POST' && request.url().includes('/access/login')) loginPosts++
  })
  await page.route('**/api/v1/admin/access/me', async (route) => {
    await new Promise<void>((resolve) => { releaseIdentity = resolve })
    return route.fulfill({ json: { id: 'google:restored', kind: 'google', email: 'restored@example.test',
      role: 'read', role_label: 'Viewer', permissions: { read: true } } })
  })
  const destination = '/?market=spot#orders'
  await page.goto(destination)
  await expect(page.getByRole('status')).toContainText('Đang khôi phục phiên đăng nhập…')
  await expect(page).toHaveURL(destination)
  await expect(page.getByRole('button', { name: 'Sign in with Google' })).toHaveCount(0)
  expect(operationalReads).toBe(0)
  await expect.poll(() => typeof releaseIdentity).toBe('function')
  releaseIdentity?.()
  await expect(page.getByRole('button', { name: /^Tài khoản / })).toBeVisible()
  await expect(page).toHaveURL(destination)
  expect(loginPosts).toBe(0)
})
