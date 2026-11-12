import { test as base, expect, type Page } from '@playwright/test'
import { contextStatus } from '../src/test/mocks/contextFixtures'
import { staleBalances } from '../src/test/mocks/adminFixtures'

export async function mockGoogle(page: Page) {
  // Mock all mounted operational reads; browser checks must not contact a real backend.
  await page.route('**/api/v1/admin/status', (route) => route.fulfill({ json: contextStatus }))
  await page.route('**/api/v1/admin/balances', (route) => route.fulfill({ json: staleBalances }))
  await page.route('**/api/v1/admin/positions', (route) => route.fulfill({ json: { owner_available: false, markets: [], server_time: '2026-11-11T00:00:00Z' } }))
  await page.route('**/api/v1/admin/statistics/liquidity*', (route) => route.fulfill({ json: { markets: {}, state: 'unavailable', measurement: 'sampled_owner_observations', session_id: null, observed_at: null } }))
  // Behavioral checks must not depend on external font availability.
  await page.route('https://fonts.googleapis.com/**', (route) => route.fulfill({ contentType: 'text/css', body: '' }))
  await page.route('https://accounts.google.com/gsi/client', (route) => route.fulfill({
    contentType: 'application/javascript',
    body: `window.google = { accounts: { id: {
      initialize(options) { this.callback = options.callback },
      renderButton(element) {
        const button = document.createElement('button'); button.textContent = 'Sign in with Google';
        button.onclick = () => this.callback({ credential: 'header.' + btoa(JSON.stringify({ exp: Math.floor(Date.now()/1000) + 3600 })) + '.signature' });
        element.appendChild(button);
      }
    } } };`,
  }))
  await page.route('**/api/v1/admin/access/{login,me}', (route) => route.fulfill({
    json: { id: 'google:test', kind: 'google', email: 'operator@example.test', role: 'read', role_label: 'Read', permissions: { read: true } },
  }))
}

export async function openAccountMenu(page: Page) {
  const trigger = page.getByRole('button', { name: /^Account / })
  if (await trigger.getAttribute('aria-expanded') !== 'true') await trigger.click()
  return page.getByRole('menu', { name: 'Account options' })
}

export async function signOut(page: Page) {
  const menu = await openAccountMenu(page)
  await menu.getByRole('button', { name: 'Sign out' }).click()
}

export async function setAccountTheme(page: Page, theme: 'light' | 'dark') {
  const menu = await openAccountMenu(page)
  await menu.getByRole('button', { name: `Use ${theme} theme` }).click()
  await page.keyboard.press('Escape')
}

export const test = base.extend<{ authenticated: void }>({
  authenticated: [async ({ page }, use) => {
    await mockGoogle(page)
    await use()
  }, { auto: true }],
})
export { expect }

export async function selectTheme(page: Page, theme: 'light' | 'dark') {
  const button = page.getByRole('button', { name: theme === 'dark' ? 'Use dark theme' : 'Use light theme', exact: true })
  if (!(await button.isVisible())) await openAccountMenu(page)
  await button.click()
  const account = page.getByRole('button', { name: /^Account / })
  if (await account.count() && await account.getAttribute('aria-expanded') === 'true') await account.click()
}
export async function openContext(page: Page) {
  const trigger = page.locator('#operational-context-trigger')
  if (await trigger.getAttribute('aria-expanded') !== 'true') await trigger.click()
}
