import { expect, test, openAccountMenu, selectTheme } from './auth-fixture'

test('starter navigation and theme switching', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page.getByRole('heading', { name: 'Shared Balances' })).toBeVisible()
  await selectTheme(page, 'dark')
  await expect(page.locator('html')).toHaveClass(/dark/)
  await page.goto('/ui-kit')
  await expect(page.getByRole('heading', { name: 'UI kit.' })).toBeVisible()
  await page.getByRole('button', { name: 'Open dialog' }).click()
  await expect(page.getByRole('dialog', { name: 'Example dialog' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

for (const width of [390, 1280]) {
  for (const theme of ['light', 'dark'] as const) {
    test(`brand home link includes visible text and supports keyboard navigation (${width}px, ${theme})`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await page.goto('/ui-kit')
      await expect(page.getByRole('heading', { name: 'UI kit.' })).toBeVisible()
      if (theme === 'dark') {
        await selectTheme(page, 'dark')
      }

      const homeLink = page.getByRole('link', { name: 'LIFE AI Market making — Go to home', exact: true })
      await expect(homeLink).toBeVisible()
      await expect(homeLink).toHaveAttribute('href', '/')
      await homeLink.focus()
      await page.keyboard.press('Enter')
      await expect(page).toHaveURL(/\/login\?redirect=/)
      await page.getByRole('button', { name: 'Sign in with Google' }).click()
      await expect(page).toHaveURL('/')
      await expect(page.getByRole('heading', { name: 'Shared Balances' })).toBeVisible()
      await page.reload()
      await openAccountMenu(page)
      await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
      await expect(homeLink).toBeVisible()
      const accountMenu = page.getByRole('menu', { name: 'Account options' })
      await expect(accountMenu.getByRole('button', { name: `Use ${theme} theme` })).toBeVisible()
      await expect(page.locator('html')).toHaveCSS('color-scheme', theme)
    })
  }
}
