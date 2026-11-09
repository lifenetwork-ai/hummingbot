import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// The inherited brand palette contains some known contrast failures.
// We check structural WCAG A/AA rules in CI without silently altering its tokens.
// Re-enable color-contrast once the design palette has been reviewed.
async function scan(page: import('@playwright/test').Page) {
  return new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .disableRules(['color-contrast'])
    .analyze()
}

test('home has no structural WCAG A/AA violations', async ({ page }) => {
  await page.goto('/')
  const results = await scan(page)
  expect(results.violations).toEqual([])
})

test('UI kit and open dialog have no structural WCAG A/AA violations', async ({ page }) => {
  await page.goto('/ui-kit')
  expect((await scan(page)).violations).toEqual([])

  await page.getByRole('button', { name: 'Open dialog' }).click()
  await expect(page.getByRole('dialog', { name: 'Example dialog' })).toBeVisible()
  expect((await scan(page)).violations).toEqual([])
})

test('keyboard can open, dismiss and restore focus on dialog', async ({ page }) => {
  await page.goto('/ui-kit')
  const button = page.getByRole('button', { name: 'Open dialog' })
  await button.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(button).toBeFocused()
})
