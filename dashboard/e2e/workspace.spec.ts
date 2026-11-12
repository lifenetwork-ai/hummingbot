import { test, expect } from '@playwright/test'
import { mockGoogle, openAccountMenu, openContext, selectTheme, signOut } from './auth-fixture'
import { contextStatus } from '../src/test/mocks/contextFixtures'
import { pendingCommand, staleBalances } from '../src/test/mocks/adminFixtures'
import { credentialReceipt, reconciliationReceipt, recoveryCheckReceipt, recoveryResetReceipt } from '../src/test/mocks/receiptFixtures'

test('public routes never mount operational polling, including while signed in', async ({ page }) => {
  await mockGoogle(page)
  let statusReads = 0
  let balanceReads = 0
  await page.route('**/api/v1/admin/status', (route) => {
    statusReads++
    return route.fulfill({ json: contextStatus })
  })
  await page.route('**/api/v1/admin/balances', (route) => {
    balanceReads++
    return route.fulfill({ json: staleBalances })
  })
  await page.goto('/login')
  await expect(page.getByRole('button', { name: 'Sign in with Google' })).toBeVisible()
  expect([statusReads, balanceReads]).toEqual([0, 0])
  await page.goto('/ui-kit')
  await expect(page.getByRole('heading', { name: 'UI kit.' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Operational context' })).toHaveCount(0)
  expect([statusReads, balanceReads]).toEqual([0, 0])
  await page.goto('/')
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page.getByText('bot-a', { exact: true })).toBeVisible()
  await expect(page.getByText('fixture-account', { exact: false })).toBeVisible()
  await page.setViewportSize({ width: 320, height: 900 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '/tmp/life-mm-workspace-mobile.png', fullPage: true })
  await selectTheme(page, 'dark')
  await page.screenshot({ path: '/tmp/life-mm-workspace-mobile-dark.png', fullPage: true })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.screenshot({ path: '/tmp/life-mm-workspace-desktop-dark.png', fullPage: true })
  await selectTheme(page, 'light')
  await page.screenshot({ path: '/tmp/life-mm-workspace-desktop.png', fullPage: true })
  await page.goto('/ui-kit')
  await expect(page.getByRole('heading', { name: 'UI kit.' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Operational context' })).toHaveCount(0)
  const stoppedReads = [statusReads, balanceReads]
  // Observe beyond the current status (15s) and balance (5s) operational intervals to prove it was removed.
  await page.waitForTimeout(16000)
  expect([statusReads, balanceReads]).toEqual(stoppedReads)
})

test('logout removes workspace polling and a new login reads fresh context without commands', async ({ page }) => {
  await mockGoogle(page)
  let reads = 0
  let botId = 'bot-before-logout'
  const writes: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && !request.url().includes('/access/login')) writes.push(request.url())
  })
  await page.route('**/api/v1/admin/status', (route) => { reads++; return route.fulfill({ json: { ...contextStatus, bot_id: botId } }) })
  await page.route('**/api/v1/admin/balances', (route) => route.fulfill({ json: staleBalances }))
  await page.goto('/?market=spot#orders')
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page.getByText(botId, { exact: true })).toBeVisible()
  await signOut(page)
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Operational context' })).toHaveCount(0)
  const stoppedReads = reads
  await page.waitForTimeout(16000)
  expect(reads).toBe(stoppedReads)
  botId = 'bot-after-login'
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page).toHaveURL('/?market=spot#orders')
  await expect(page.getByText(botId, { exact: true })).toBeVisible()
  await expect(page.getByText('bot-before-logout', { exact: true })).toHaveCount(0)
  expect(writes).toEqual([])
})
test('stale timestamp fits the authenticated workspace at 320px', async ({ page }) => {
  await mockGoogle(page)
  await page.route('**/api/v1/admin/status', (route) => route.fulfill({ json: { ...contextStatus,
    observation: { observed_at: '2026-11-10T12:00:00.123456Z', stale: true, session_matches: true, reason_code: null } } }))
  await page.route('**/api/v1/admin/balances', (route) => route.fulfill({ json: staleBalances }))
  await page.setViewportSize({ width: 320, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page.getByText('bot-a', { exact: true })).toBeVisible()
  await expect(page.getByText('Stale data').first()).toBeVisible()
  await page.screenshot({ path: '/tmp/life-mm-ui-handoff-stale-mobile.png', fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('retained receipts can be inspected without replaying commands across reload', async ({ page }) => {
  await mockGoogle(page)
  const writes: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && !request.url().includes('/access/login')) writes.push(request.url())
  })
  await page.route('**/api/v1/admin/status', (route) => route.fulfill({ json: contextStatus }))
  await page.route('**/api/v1/admin/balances', (route) => route.fulfill({ json: staleBalances }))
  await page.route('**/api/v1/admin/commands/cmd-retained-test-1234', (route) =>
    route.fulfill({ json: { ...pendingCommand, id: 'cmd-retained-test-1234', status: 'accepted' } })
  )

  // Pre-seed localStorage with tracked receipt for this bot and user scope
  await page.addInitScript(() => {
    const scopeKey = 'life-mm.accepted-receipts.v1:%2Fapi%2Fv1%2Fadmin:google%3Atest:bot-a'
    window.localStorage.setItem(
      scopeKey,
      JSON.stringify({
        version: 1,
        receipts: [{ kind: 'command', id: 'cmd-retained-test-1234', acceptedAt: Date.now() }],
      })
    )
  })

  await page.goto('/')
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await expect(page.getByText('bot-a', { exact: true })).toBeVisible()

  const receiptBtn = page.getByTitle('command: cmd-retained-test-1234')
  await openContext(page)
  await expect(receiptBtn).toBeVisible()

  await receiptBtn.click()
  await expect(page.getByText('cmd-retained-test-1234')).toBeVisible()
  await expect(page.getByText('accepted')).toBeVisible()
  await expect(page.getByText('fixture-session', { exact: true })).toBeVisible()

  await page.reload()
  await openAccountMenu(page)
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  await expect(page.getByText('bot-a', { exact: true })).toBeVisible()
  const reloadedReceiptBtn = page.getByTitle('command: cmd-retained-test-1234')
  await openContext(page)
  await expect(reloadedReceiptBtn).toBeVisible()

  expect(writes).toEqual([])
})

test('receipt inspection keeps processing success separate from business outcome and hides tokens', async ({ page }) => {
  await mockGoogle(page)
  const writes: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && !request.url().includes('/access/login')) writes.push(request.url())
  })
  await page.route('**/api/v1/admin/access/{login,me}', (route) => route.fulfill({ json: {
    id: 'google:test', kind: 'google', email: 'admin@example.test', role: 'admin', role_label: 'Administrator',
    permissions: { read: true, operate: true, manage_recovery: true, manage_credentials: true, manage_accounts: true },
  } }))
  await page.route('**/api/v1/admin/status', (route) => route.fulfill({ json: contextStatus }))
  await page.route('**/api/v1/admin/balances', (route) => route.fulfill({ json: staleBalances }))
  await page.route('**/api/v1/admin/credentials/operations/cred-inspect', (route) => route.fulfill({ json: { ...credentialReceipt, id: 'cred-inspect' } }))
  await page.route('**/api/v1/admin/reconciliation/rec-inspect', (route) => route.fulfill({ json: { ...reconciliationReceipt, id: 'rec-inspect' } }))
  await page.route('**/api/v1/admin/recovery/operations/review-inspect', (route) => route.fulfill({ json: { ...recoveryCheckReceipt, id: 'review-inspect' } }))
  await page.route('**/api/v1/admin/recovery/operations/reset-inspect', (route) => route.fulfill({ json: { ...recoveryResetReceipt, id: 'reset-inspect' } }))
  await page.addInitScript(() => {
    const refs = [
      { kind: 'credential', id: 'cred-inspect' }, { kind: 'reconciliation', id: 'rec-inspect' },
      { kind: 'recovery', id: 'review-inspect' }, { kind: 'recovery', id: 'reset-inspect' },
    ]
    localStorage.setItem('life-mm.accepted-receipts.v1:%2Fapi%2Fv1%2Fadmin:google%3Atest:bot-a',
      JSON.stringify({ version: 1, receipts: refs.map((ref) => ({ ...ref, acceptedAt: Date.now() })) }))
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  const details = page.getByLabel('Tracked operation details')
  await openContext(page)
  await page.getByTitle('credential: cred-inspect').click()
  await expect(details.getByText('exchange_cleanup', { exact: true })).toBeVisible()
  await page.getByTitle('reconciliation: rec-inspect').click()
  await expect(details.getByText('Receipt progress: succeeded', { exact: true })).toBeVisible()
  await expect(details.getByText('Reconciliation result: Mismatch', { exact: true })).toBeVisible()
  await page.getByTitle('recovery: review-inspect').click()
  await expect(details.getByText('Receipt progress: succeeded', { exact: true })).toBeVisible()
  await expect(details.getByText('Backend has not confirmed recoverable.', { exact: true })).toBeVisible()
  await expect(details.getByText('Recovered per receipt.', { exact: true })).toHaveCount(0)
  await page.getByTitle('recovery: reset-inspect').click()
  await expect(details.getByText('Recovered per receipt.', { exact: true })).toBeVisible()
  await expect(page.locator('body')).not.toContainText('SECRET-FIXTURE-')
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('SECRET-FIXTURE-')
  expect(writes).toEqual([])
})

test('receipt completion refreshes observations without closing its result or replaying writes', async ({ page }) => {
  await mockGoogle(page)
  let receiptGets = 0
  let statusGets = 0
  const writes: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && !request.url().includes('/access/login')) writes.push(request.url())
  })
  await page.route('**/api/v1/admin/status', (route) => { statusGets++; return route.fulfill({ json: contextStatus }) })
  await page.route('**/api/v1/admin/balances', (route) => route.fulfill({ json: staleBalances }))
  await page.route('**/api/v1/admin/commands/cmd-terminal', (route) => {
    receiptGets++
    return route.fulfill({ json: { ...pendingCommand, id: 'cmd-terminal', status: receiptGets === 1 ? 'accepted' : 'succeeded' } })
  })
  await page.addInitScript(() => {
    localStorage.setItem('life-mm.accepted-receipts.v1:%2Fapi%2Fv1%2Fadmin:google%3Atest:bot-a',
      JSON.stringify({ version: 1, receipts: [{ kind: 'command', id: 'cmd-terminal', acceptedAt: Date.now() }] }))
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Sign in with Google' }).click()
  await openContext(page)
  await page.getByTitle('command: cmd-terminal').click()
  const details = page.getByLabel('Tracked operation details')
  await expect(details.getByText('Receipt progress: accepted', { exact: true })).toBeVisible()
  await expect(details.getByText('Receipt progress: succeeded', { exact: true })).toBeVisible()
  await expect.poll(() => statusGets).toBeGreaterThanOrEqual(2)
  await details.getByRole('button', { name: 'Close' }).click()
  await expect(details).toHaveCount(0)
  const completedReads = receiptGets
  // Prove receipt polling is removed after deselection (cadence is 1s).
  await page.waitForTimeout(1300)
  expect(receiptGets).toBe(completedReads)
  expect(writes).toEqual([])
})
