import { expect, test } from '@playwright/test'

// Baselines are intentionally not invented. Generate once using
// `pnpm run test:visual:update` on a Linux/Chromium environment and commit PNGs.
// CI activates these checks automatically as soon as snapshots are present.
test.skip(!process.env.VISUAL_BASELINES_READY, 'Create visual baselines first; see docs/TESTING.md')

async function settle(page: import('@playwright/test').Page) {
  await page.evaluate(() => document.fonts.ready)
}

test('@visual home matches saved light theme snapshot', async ({ page }) => {
  await page.goto('/')
  await settle(page)
  await expect(page).toHaveScreenshot('home-light.png', { fullPage: true })
})

test('@visual UI kit matches saved light and dark snapshots', async ({ page }) => {
  await page.goto('/ui-kit')
  await settle(page)
  await expect(page).toHaveScreenshot('ui-kit-light.png', { fullPage: true })

  await page.getByRole('button', { name: 'Use dark theme' }).click()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await expect(page).toHaveScreenshot('ui-kit-dark.png', { fullPage: true })
})
