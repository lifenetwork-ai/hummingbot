import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { staleBalances } from '../../test/mocks/adminFixtures'
import { contextPrincipal, contextStatus } from '../../test/mocks/contextFixtures'
import { deriveOperationalContext, type ContextSource, type OperationalContextInput } from './context'
import { ContextBar, type ContextBarProps } from './ContextBar'
import type { TrackedReceiptInspection } from '../../core/api/hooks'
import type { ReceiptOutcome } from '../../core/api/receipt-inspection'

const ready = <T,>(data: T): ContextSource<T> => ({ phase: 'ready', data, error: null, refreshing: false })
const input: OperationalContextInput = {
  identity: contextPrincipal,
  status: ready(contextStatus),
  balances: ready(staleBalances),
}
function renderContext(
  overrides: Partial<OperationalContextInput> = {},
  barProps: Partial<ContextBarProps> = { defaultOpen: true }
) {
  const context = deriveOperationalContext({ ...input, ...overrides })
  return render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" defaultOpen={true} {...barProps} />)
}

describe('ContextBar', () => {
  it('renders backend bot, account, market, identity, and action availability from its model', () => {
    renderContext({ status: ready({ ...contextStatus, markets: [{ admitted: false, connector: 'okx', inventory_skew: null, inventory_reduction: null,
      market: 'spot', pair: 'LIFE-USDT', quote_guard: null, ready: false, reason: 'not_started',
      reference_price: null, spread_policy: null }] }) })
    expect(screen.getByText('bot-a')).toBeInTheDocument()
    expect(screen.getByText(/fixture-account/)).toBeInTheDocument()
    expect(screen.getByText('okx · spot · LIFE-USDT')).toBeInTheDocument()
    expect(screen.getByText('Operator · google:operator')).toBeInTheDocument()
    expect(screen.getByText('start: blocked')).toBeInTheDocument()
    expect(screen.getByText('Session is active.')).toBeInTheDocument()
  })

  it('shows missing status and balance sources independently', () => {
    renderContext({
      status: { phase: 'error', error: new Error('offline'), refreshing: false },
      balances: { phase: 'error', error: new Error('offline'), refreshing: false },
    })
    expect(screen.getByText('Failed to read status')).toBeInTheDocument()
    expect(screen.getByText('Failed to read account status')).toBeInTheDocument()
    expect(screen.getByText('Cannot read operational capabilities; please reload status.')).toBeInTheDocument()
  })

  it('shows status source error while balances source is ready', () => {
    renderContext({
      status: { phase: 'error', error: new Error('status down'), refreshing: false },
      balances: ready(staleBalances),
    })
    expect(screen.getByText('Failed to read status')).toBeInTheDocument()
    expect(screen.getByText(/fixture-account/)).toBeInTheDocument()
    expect(screen.queryByText('Failed to read account status')).not.toBeInTheDocument()
  })

  it('shows balances source error while status source is ready', () => {
    renderContext({
      status: ready(contextStatus),
      balances: { phase: 'error', error: new Error('balances down'), refreshing: false },
    })
    expect(screen.getByText('bot-a')).toBeInTheDocument()
    expect(screen.getByText('Failed to read account status')).toBeInTheDocument()
    expect(screen.queryByText('Failed to read status')).not.toBeInTheDocument()
    expect(screen.getByText('stop: allowed')).toBeInTheDocument()
  })

  it('retains last-known bot context with an error label and does not claim capabilities', () => {
    renderContext({ status: { phase: 'error', data: contextStatus, error: new Error('offline'), refreshing: false } })
    expect(screen.getByText('bot-a')).toBeInTheDocument()
    expect(screen.getByText(/Retaining last-read data/)).toBeInTheDocument()
    expect(screen.getByText('Cannot read operational capabilities; please reload status.')).toBeInTheDocument()
  })

  it('shows unknown environment and unverified account from the source model', () => {
    const account = staleBalances.accounts[0]
    renderContext({
      status: ready({ ...contextStatus, process_identity: null, owner_identity: null }),
      balances: ready({ ...staleBalances, accounts: [{ ...account, account_id: null, identity_verified: false, environment: 'unknown', balances: [] }] }),
    })
    expect(screen.getAllByText('Unknown')).toHaveLength(2)
    expect(screen.getByText('Unverified account identity')).toBeInTheDocument()
  })

  it('shows a mismatch only for a verified current account observation', () => {
    const account = staleBalances.accounts[0]
    renderContext({
      balances: ready({ ...staleBalances, owner_available: true, accounts: [{ ...account, environment: 'live',
        balances: account.balances.map((balance) => ({ ...balance, stale: false, present_in_latest_snapshot: true })) }] }),
    })
    expect(screen.getByRole('alert')).toHaveTextContent('backend environment identifiers do not agree')
  })

  it('shows every account without selecting a trading account', () => {
    const account = staleBalances.accounts[0]
    renderContext({ balances: ready({ ...staleBalances, owner_available: true, accounts: [
      { ...account, environment: 'demo' },
      { ...account, account_id: 'second-account', environment: 'live' },
    ] }) })
    expect(screen.getByText('2 accounts returned by backend')).toBeInTheDocument()
    expect(screen.getByText('fixture-account · Demo')).toBeInTheDocument()
    expect(screen.getByText('second-account · Live')).toBeInTheDocument()
  })

  it('does not attribute a process/owner mismatch to a nonexistent exchange account', () => {
    renderContext({ status: ready({ ...contextStatus,
      owner_identity: { ...contextStatus.owner_identity!, trading_environment: 'live' } }),
    balances: ready({ ...staleBalances, accounts: [] }) })
    expect(screen.getByRole('alert')).not.toHaveTextContent('verified account')
    expect(screen.getByRole('alert')).toHaveTextContent('Mismatch')
  })

  it('renders observation timestamp and freshness badge when observation is available', () => {
    renderContext({
      status: ready({
        ...contextStatus,
        observation: { observed_at: '2026-11-10T12:00:00Z', reason_code: null, stale: true, session_matches: true },
      }),
    })
    expect(screen.getByText('2026-11-10T12:00:00Z')).toBeInTheDocument()
    expect(screen.getAllByText('Stale data').length).toBeGreaterThanOrEqual(1)
  })

  it('does not label a failed status refresh fresh', () => {
    const context = deriveOperationalContext({
      identity: contextPrincipal,
      status: { phase: 'error', data: contextStatus, error: new Error('offline'), refreshing: false },
      balances: { phase: 'ready', data: staleBalances, error: null, refreshing: false },
    })
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" defaultOpen={true} />)
    expect(screen.queryByText('Recently updated', { exact: true })).not.toBeInTheDocument()
    expect(screen.getAllByText('Stale data').length).toBeGreaterThanOrEqual(1)
  })

  it('discloses stale account observations even for a single account', () => {
    const context = deriveOperationalContext({
      identity: contextPrincipal,
      status: { phase: 'ready', data: contextStatus, error: null, refreshing: false },
      balances: { phase: 'ready', data: staleBalances, error: null, refreshing: false },
    })
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" defaultOpen={true} />)
    expect(context.accounts[0].observation).toBe('stale')
    expect(screen.getByText('Stale data', { exact: true })).toBeInTheDocument()
  })

  it('offers an interactive way to resume a retained operation', () => {
    const context = deriveOperationalContext({
      identity: contextPrincipal,
      status: { phase: 'ready', data: contextStatus, error: null, refreshing: false },
      balances: { phase: 'ready', data: staleBalances, error: null, refreshing: false },
    })
    const { container } = render(
      <ContextBar
        context={context}
        apiBaseUrl="/api/v1/admin"
        trackedReceipts={[{ kind: 'command', id: 'cmd-retained-operation' }]}
      />
    )
    expect(container.querySelectorAll('button, a').length).toBeGreaterThan(0)
  })

  it('renders distinct badges for unknown, session mismatch and fresh observation states', () => {
    const unknownCtx = deriveOperationalContext({
      ...input,
      status: ready({ ...contextStatus, observation: null, owner_available: false }),
    })
    const { rerender } = render(<ContextBar context={unknownCtx} apiBaseUrl="/api/v1/admin" defaultOpen={true} />)
    expect(screen.getByText('No timestamp')).toBeInTheDocument()
    expect(screen.getByText('Unknown')).toBeInTheDocument()

    const mismatchCtx = deriveOperationalContext({
      ...input,
      status: ready({
        ...contextStatus,
        observation: { observed_at: '2026-11-10T12:00:00Z', reason_code: null, stale: false, session_matches: false },
      }),
    })
    rerender(<ContextBar context={mismatchCtx} apiBaseUrl="/api/v1/admin" defaultOpen={true} />)
    expect(screen.getByText('Session mismatch')).toBeInTheDocument()

    const freshCtx = deriveOperationalContext({
      ...input,
      status: ready({
        ...contextStatus,
        observation: { observed_at: '2026-11-10T12:00:00Z', reason_code: null, stale: false, session_matches: true },
      }),
    })
    rerender(<ContextBar context={freshCtx} apiBaseUrl="/api/v1/admin" defaultOpen={true} />)
    expect(screen.getByText('Recently updated')).toBeInTheDocument()
  })

  it('renders tracked operation references and allows keyboard selection and inspection', async () => {
    const user = userEvent.setup()
    const context = deriveOperationalContext(input)
    const longId = 'cmd-1234567890abcdef1234567890abcdef'
    const { rerender } = render(
      <ContextBar
        context={context}
        apiBaseUrl="/api/v1/admin"
        defaultOpen={true}
        trackedReceipts={[
          { kind: 'command', id: longId },
          { kind: 'recovery', id: 'rec-0987654321fedcba' },
        ]}
      />
    )
    expect(screen.getByText(/Tracked prior operations/)).toBeInTheDocument()
    const cmdBtn = screen.getByRole('button', { name: /command: cmd-12345678…/ })
    const recBtn = screen.getByRole('button', { name: /recovery: rec-09876543…/ })
    expect(cmdBtn).toBeInTheDocument()
    expect(recBtn).toBeInTheDocument()
    expect(cmdBtn).toHaveAttribute('aria-pressed', 'false')

    await user.click(cmdBtn)
    expect(cmdBtn).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Tracked operation details')).toBeInTheDocument()
    expect(screen.getByText(longId)).toBeInTheDocument()

    rerender(
      <ContextBar
        context={context}
        apiBaseUrl="/api/v1/admin"
        defaultOpen={true}
        trackedReceipts={[
          { kind: 'command', id: longId },
          { kind: 'recovery', id: 'rec-0987654321fedcba' },
        ]}
        selectedReceipt={{ kind: 'command', id: longId }}
        receiptInspection={{
          reference: { kind: 'command', id: longId },
          phase: 'ready',
          status: 'succeeded',
          detail: 'Operation complete',
          error: null,
          outcome: { kind: 'command', action: 'stop', sessionId: 'session-1', error: null },
        }}
      />
    )
    expect(screen.getByText('Receipt progress: succeeded')).toBeInTheDocument()
    expect(screen.getByText(/does not confirm current order status or position/)).toBeInTheDocument()

    const closeBtn = screen.getByRole('button', { name: 'Close' })
    await user.click(closeBtn)
  })

  function renderReceipt(status: string, outcome: ReceiptOutcome) {
    const reference = { kind: outcome.kind, id: 'receipt-1' } as const
    const inspection: TrackedReceiptInspection = { reference, phase: 'ready', status, error: null, outcome }
    render(<ContextBar context={deriveOperationalContext(input)} apiBaseUrl="/api/v1/admin"
      trackedReceipts={[reference]} selectedReceipt={reference} receiptInspection={inspection} defaultOpen={true} />)
  }

  it('keeps a succeeded reconciliation receipt distinct from mismatch outcome', () => {
    renderReceipt('succeeded', { kind: 'reconciliation', state: 'mismatch', reasons: ['missing order'] })
    expect(screen.getByText('Receipt progress: succeeded')).toBeInTheDocument()
    expect(screen.getByText('Reconciliation result: Mismatch')).toBeInTheDocument()
    expect(screen.getByText('missing order')).toBeInTheDocument()
  })

  it('shows a succeeded but blocked recovery as not recoverable', () => {
    renderReceipt('succeeded', { kind: 'recovery', action: 'recovery_check', stage: 'blocked', canRecover: false,
      blockers: [{ code: 'owner_unavailable', detail: 'Owner is offline.', next_action: 'wait' }] })
    expect(screen.getByText('Receipt progress: succeeded')).toBeInTheDocument()
    expect(screen.getByText('Backend has not confirmed recoverable.')).toBeInTheDocument()
    expect(screen.getByText('owner_unavailable')).toBeInTheDocument()
    expect(screen.getByText(/Owner is offline./)).toBeInTheDocument()
  })

  it('shows recovery success only when succeeded and stage is recovered', () => {
    renderReceipt('succeeded', { kind: 'recovery', action: 'recovery_reset', stage: 'recovered', canRecover: false, blockers: [] })
    expect(screen.getByText('Recovered per receipt.')).toBeInTheDocument()
  })

  it('keeps pending receipt progress separate from the final credential stage', () => {
    renderReceipt('applying', { kind: 'credential', action: 'credentials_disconnect', stage: 'canceling_orders',
      operatorActionRequired: false, reasonCode: null, error: null })
    expect(screen.getByText('Receipt progress: applying')).toBeInTheDocument()
    expect(screen.getByText('Stage')).toBeInTheDocument()
    expect(screen.getByText('canceling_orders')).toBeInTheDocument()
    expect(screen.getByText(/does not yet confirm current connection state/)).toBeInTheDocument()
  })

  it('shows receipt read errors separately from operation outcome', () => {
    const reference = { kind: 'command', id: 'cmd-fail-read' } as const
    const inspection: TrackedReceiptInspection = { reference, phase: 'error', error: new Error('receipt unavailable') }
    render(<ContextBar context={deriveOperationalContext(input)} apiBaseUrl="/api/v1/admin" trackedReceipts={[reference]}
      selectedReceipt={reference} receiptInspection={inspection} defaultOpen={true} />)
    expect(screen.getByText('receipt unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/Reconciliation result/)).not.toBeInTheDocument()
  })

  it('renders technical diagnostics details disclosure', () => {
    const context = deriveOperationalContext(input)
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" defaultOpen={true} />)
    expect(screen.getByText('Technical & diagnostic details')).toBeInTheDocument()
    expect(screen.getByText('Control Version:')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('displays non-shifting refreshing overlays and accessible status when refreshing', () => {
    const context = deriveOperationalContext({
      ...input,
      status: { ...input.status, refreshing: true },
      balances: { ...input.balances, refreshing: true },
    })
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" defaultOpen={true} />)

    // Badge overwrites freshness label with syncing status
    expect(screen.getByText('Syncing…')).toBeInTheDocument()
    expect(screen.queryByText('Recently updated')).not.toBeInTheDocument()

    // Card overlays
    expect(screen.getByText('Syncing bot & balances…')).toBeInTheDocument()
    expect(screen.getByText('Updating permissions…')).toBeInTheDocument()

    // Screen reader live status
    expect(screen.getByRole('status')).toHaveTextContent('Updating bot status.')
    expect(screen.getByRole('status')).toHaveTextContent('Updating account balances.')
  })

  // --- Specific Disclosure / Secondary Info Tests ---

  it('is collapsed by default and presents summary indicator without detail cards', () => {
    const context = deriveOperationalContext(input)
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)

    // Header title and trigger button
    expect(screen.getByText('Operational context')).toBeInTheDocument()
    const trigger = screen.getByRole('button', { name: /Details/ })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(trigger).toHaveAttribute('aria-controls', 'operational-context-panel')

    // Essential summary signals visible on the compact bar
    expect(screen.getByText('bot-a')).toBeInTheDocument()
    expect(screen.getByText(/fixture-account/)).toBeInTheDocument()
    expect(screen.getByText('Demo')).toBeInTheDocument()
    expect(screen.getByText('Recently updated')).toBeInTheDocument()

    // Detailed cards remain hidden when collapsed
    expect(screen.queryByText('Connection and bot')).not.toBeInTheDocument()
    expect(screen.queryByText('Permissions and operational capabilities')).not.toBeInTheDocument()
    expect(screen.queryByText('Technical & diagnostic details')).not.toBeInTheDocument()
  })

  it('opens and closes via mouse click on disclosure trigger', async () => {
    const user = userEvent.setup()
    const context = deriveOperationalContext(input)
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)

    const trigger = screen.getByRole('button', { name: /Details/ })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')

    // Open disclosure
    await user.click(trigger)
    expect(screen.getByRole('button', { name: /Collapse/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Connection and bot')).toBeInTheDocument()
    expect(screen.getByText('Permissions and operational capabilities')).toBeInTheDocument()
    expect(screen.getByText('Technical & diagnostic details')).toBeInTheDocument()

    // Close disclosure
    await user.click(screen.getByRole('button', { name: /Collapse/ }))
    expect(screen.getByRole('button', { name: /Details/ })).toHaveAttribute('aria-expanded', 'false')
    await waitFor(() => expect(screen.queryByText('Connection and bot')).not.toBeInTheDocument())
  })

  it('toggles open and close via Enter and Space keyboard interactions', async () => {
    const user = userEvent.setup()
    const context = deriveOperationalContext(input)
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)

    const trigger = screen.getByRole('button', { name: /Details/ })
    trigger.focus()
    expect(trigger).toHaveFocus()

    // Press Enter to open
    await user.keyboard('{Enter}')
    expect(screen.getByRole('button', { name: /Collapse/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Connection and bot')).toBeInTheDocument()

    // Press Space to close
    await user.keyboard(' ')
    expect(screen.getByRole('button', { name: /Details/ })).toHaveAttribute('aria-expanded', 'false')
    await waitFor(() => expect(screen.queryByText('Connection and bot')).not.toBeInTheDocument())
  })

  it('closes expanded drawer when Escape key is pressed', async () => {
    const user = userEvent.setup()
    const context = deriveOperationalContext(input)
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)

    const trigger = screen.getByRole('button', { name: /Details/ })
    await user.click(trigger)
    expect(screen.getByRole('button', { name: /Collapse/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Connection and bot')).toBeInTheDocument()

    // Press Escape
    await user.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: /Details/ })).toHaveAttribute('aria-expanded', 'false')
    await waitFor(() => expect(screen.queryByText('Connection and bot')).not.toBeInTheDocument())
  })

  it('prominently indicates stale state without auto-opening panel', () => {
    const context = deriveOperationalContext({
      ...input,
      status: ready({
        ...contextStatus,
        observation: { observed_at: '2026-11-10T12:00:00Z', reason_code: null, stale: true, session_matches: true },
      }),
    })
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)

    // Stale indicator on compact bar
    expect(screen.getByText('Stale data')).toBeInTheDocument()

    // Panel remains closed
    const trigger = screen.getByRole('button', { name: /Details/ })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Connection and bot')).not.toBeInTheDocument()
  })

  it('prominently indicates error state without auto-opening panel', () => {
    const context = deriveOperationalContext({
      ...input,
      status: { phase: 'error', data: contextStatus, error: new Error('connection lost'), refreshing: false },
    })
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)

    // Prominent error badge on compact bar
    expect(screen.getByText('Cannot verify bot')).toBeInTheDocument()

    // Panel remains closed
    const trigger = screen.getByRole('button', { name: /Details/ })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Connection and bot')).not.toBeInTheDocument()
  })

  it('prominently indicates session mismatch and environment mismatch without auto-opening panel', () => {
    const account = staleBalances.accounts[0]
    const context = deriveOperationalContext({
      ...input,
      status: ready({
        ...contextStatus,
        observation: { observed_at: '2026-11-10T12:00:00Z', reason_code: null, stale: false, session_matches: false },
      }),
      balances: ready({
        ...staleBalances,
        owner_available: true,
        accounts: [{
          ...account,
          environment: 'live',
          balances: account.balances.map((b) => ({ ...b, stale: false, present_in_latest_snapshot: true })),
        }],
      }),
    })
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)

    // Warning indicators on compact bar
    expect(screen.getByText('Session mismatch')).toBeInTheDocument()
    expect(screen.getByText('Environment mismatch')).toBeInTheDocument()

    // Panel remains closed
    const trigger = screen.getByRole('button', { name: /Details/ })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Connection and bot')).not.toBeInTheDocument()
  })

  it('prominently indicates unknown observation state without auto-opening panel', () => {
    const context = deriveOperationalContext({
      ...input,
      status: ready({ ...contextStatus, observation: null, owner_available: false }),
    })
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)

    // Unknown indicator
    expect(screen.getByText('Unknown')).toBeInTheDocument()

    // Panel remains closed
    const trigger = screen.getByRole('button', { name: /Details/ })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Connection and bot')).not.toBeInTheDocument()
  })

  it('allows selecting receipt from collapsed bar and automatically opens panel for inspection', async () => {
    const user = userEvent.setup()
    const context = deriveOperationalContext(input)
    const receiptRef = { kind: 'command' as const, id: 'cmd-retained-test-456' }
    const onSelectReceipt = vi.fn()

    render(
      <ContextBar
        context={context}
        apiBaseUrl="/api/v1/admin"
        trackedReceipts={[receiptRef]}
        onSelectReceipt={onSelectReceipt}
        receiptInspection={{
          reference: receiptRef,
          phase: 'ready',
          status: 'accepted',
          detail: 'Command accepted',
          error: null,
          outcome: { kind: 'command', action: 'start', sessionId: 'sess-abc', error: null },
        }}
      />
    )

    // Open the panel via Details button to inspect tracked receipts
    const toggleBtn = screen.getByRole('button', { name: /Details/ })
    await user.click(toggleBtn)

    const receiptBtn = screen.getByRole('button', { name: /command: cmd-retained…/ })
    expect(receiptBtn).toBeInTheDocument()

    // Clicking receipt pill selects it and displays inspection
    await user.click(receiptBtn)
    expect(onSelectReceipt).toHaveBeenCalledWith(receiptRef)
    expect(screen.getByRole('button', { name: /Collapse/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByLabelText('Tracked operation details')).toBeInTheDocument()
    expect(screen.getByText('cmd-retained-test-456')).toBeInTheDocument()
    expect(screen.getByText('Receipt progress: accepted')).toBeInTheDocument()
  })

  it('supports controlled open state via open and onOpenChange', async () => {
    const user = userEvent.setup()
    const context = deriveOperationalContext(input)
    const onOpenChange = vi.fn()

    const { rerender } = render(
      <ContextBar context={context} apiBaseUrl="/api/v1/admin" open={false} onOpenChange={onOpenChange} />
    )

    const trigger = screen.getByRole('button', { name: /Details/ })
    await user.click(trigger)
    expect(onOpenChange).toHaveBeenCalledWith(true)

    // Rerender as open
    rerender(<ContextBar context={context} apiBaseUrl="/api/v1/admin" open={true} onOpenChange={onOpenChange} />)
    expect(screen.getByRole('button', { name: /Collapse/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Connection and bot')).toBeInTheDocument()
  })
})
