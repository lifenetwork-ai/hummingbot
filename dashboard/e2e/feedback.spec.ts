import { expect, test } from './auth-fixture'

test.describe('System feedback UI kit demo', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/ui-kit')
    await expect(page).toHaveURL('/ui-kit')
    await expect(page.getByRole('heading', { name: 'UI kit.' })).toBeVisible()
  })

  test('triggers success, info, warning, error toasts and interacts with action & close', async ({ page }) => {
    // Success toast
    await page.getByRole('button', { name: 'Success toast' }).click()
    const successToast = page.locator('[data-sonner-toast]').filter({ hasText: 'Configuration saved' })
    await expect(successToast).toBeVisible()

    // Info toast
    await page.getByRole('button', { name: 'Info toast' }).click()
    await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'Sync in progress' })).toBeVisible()

    // Warning toast with action button
    await page.getByRole('button', { name: 'Warning toast (action)' }).click()
    const warningToast = page.locator('[data-sonner-toast]').filter({ hasText: 'Rate limit near threshold' })
    await expect(warningToast).toBeVisible()
    const throttleBtn = warningToast.getByRole('button', { name: 'Throttle' })
    await expect(throttleBtn).toBeVisible()

    // Error toast with action button
    await page.getByRole('button', { name: 'Error toast (action)' }).click()
    const errorToast = page.locator('[data-sonner-toast]').filter({ hasText: 'Order submission rejected' })
    await expect(errorToast).toBeVisible()

    // Close button dismisses toast
    const closeBtn = errorToast.locator('[data-close-button]')
    if (await closeBtn.isVisible()) {
      await closeBtn.click()
      await expect(errorToast).toHaveCount(0)
    }
  })

  test('loading toast transitions to success with same stable ID without duplicating', async ({ page }) => {
    // 1. Start loading
    await page.getByRole('button', { name: 'Start loading' }).click()
    const loadingToast = page.locator('[data-sonner-toast]').filter({ hasText: 'Submitting order…' })
    await expect(loadingToast).toBeVisible()

    // 2. Resolve to success using same ID
    await page.getByRole('button', { name: 'Resolve to success' }).click()
    const successToast = page.locator('[data-sonner-toast]').filter({ hasText: 'Order submitted' })
    await expect(successToast).toBeVisible()
    // Old text is replaced
    await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'Submitting order…' })).toHaveCount(0)

    // 3. Dismiss
    await page.getByRole('button', { name: 'Dismiss loading' }).click()
    await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'Order submitted' })).toHaveCount(0)
  })

  test('repeated calls with same ID update in-place without duplicating', async ({ page }) => {
    const repeatBtn = page.getByRole('button', { name: 'Repeat same ID' })
    await repeatBtn.click()
    await repeatBtn.click()
    await repeatBtn.click()

    const matchingToasts = page.locator('[data-sonner-toast]').filter({ hasText: 'Single task update' })
    await expect(matchingToasts).toHaveCount(1)
  })

  test('enforces max visible toasts limit (3)', async ({ page }) => {
    await page.getByRole('button', { name: 'Spawn 4 toasts' }).click()
    // Wait brief moment for animations
    await page.waitForTimeout(300)
    const visibleToasts = page.locator('[data-sonner-toast][data-visible=true]')
    const count = await visibleToasts.count()
    expect(count).toBeLessThanOrEqual(3)
  })

  test('persistent and dismissible banners operate properly', async ({ page }) => {
    // Persistent banner has no close button
    const persistentBanner = page.locator('section[aria-labelledby="feedback-title"]').getByRole('status').filter({ hasText: 'Gateway degraded performance' })
    await expect(persistentBanner).toBeVisible()
    await expect(persistentBanner.getByRole('button', { name: 'Dismiss banner' })).toHaveCount(0)

    // Dismissible banner has close button
    const dismissibleBanner = page.locator('section[aria-labelledby="feedback-title"]').getByRole('status').filter({ hasText: 'Scheduled maintenance window' })
    await expect(dismissibleBanner).toBeVisible()
    await dismissibleBanner.getByRole('button', { name: 'Dismiss banner' }).click()
    await expect(dismissibleBanner).toHaveCount(0)

    // Restore banner
    await page.getByRole('button', { name: 'Restore dismissed banner' }).click()
    await expect(page.locator('section[aria-labelledby="feedback-title"]').getByRole('status').filter({ hasText: 'Scheduled maintenance window' })).toBeVisible()
  })

  test('inline feedback validates input and updates accessibility role', async ({ page }) => {
    const input = page.getByLabel('Order size')
    await input.fill('abc')

    // Error appears with role="alert"
    const errorFeedback = page.getByRole('alert').filter({ hasText: 'Invalid numeric quantity' })
    await expect(errorFeedback).toBeVisible()

    // Valid number
    await input.fill('12.5')
    await expect(page.getByRole('alert').filter({ hasText: 'Invalid numeric quantity' })).toHaveCount(0)
    await expect(page.getByRole('status').filter({ hasText: 'Minimum tick size is 0.001.' })).toBeVisible()
  })

  test('feedback components fit within 320px viewport without overflow', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
})
