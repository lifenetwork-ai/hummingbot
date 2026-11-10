import { test, expect } from '@playwright/test'
import { mockGoogle } from './auth-fixture'

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

test('protects home deep links, returns after login, and clears session on logout/reload', async ({ page }) => {
  const destination = '/?market=spot#orders'
  await page.goto(destination)
  await expect(page).toHaveURL(/\/login\?redirect=/)
  await expect(page.getByRole('heading', { name: 'A clean canvas for what comes next.' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page).toHaveURL(destination)
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in to dashboard' })).toBeVisible()
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page).toHaveURL(destination)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Sign in to dashboard' })).toBeVisible()
  expect(await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)].filter((key) => /token|credential|auth/i.test(key)))).toEqual([])
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
