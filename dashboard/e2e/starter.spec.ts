import { expect, test } from '@playwright/test'

test('starter navigation and theme switching', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'A clean canvas for what comes next.' })).toBeVisible()
  await page.getByRole('button', { name: 'Use dark theme' }).click()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await page.getByRole('link', { name: 'Explore UI kit' }).click()
  await expect(page.getByRole('heading', { name: 'UI kit.' })).toBeVisible()
  await page.getByRole('button', { name: 'Open dialog' }).click()
  await expect(page.getByRole('dialog', { name: 'Example dialog' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
})
