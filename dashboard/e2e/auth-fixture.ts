import { test as base, expect, type Page } from '@playwright/test'

export async function mockGoogle(page: Page) {
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

export const test = base.extend<{ authenticated: void }>({
  authenticated: [async ({ page }, use) => {
    await mockGoogle(page)
    await use()
  }, { auto: true }],
})
export { expect }
