import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { staleBalances } from '../../test/mocks/adminFixtures'
import { contextPrincipal, contextStatus } from '../../test/mocks/contextFixtures'
import { deriveOperationalContext, type ContextSource, type OperationalContextInput } from './context'
import { ContextBar } from './ContextBar'
import type { TrackedReceiptInspection } from '../../core/api/hooks'
import type { ReceiptOutcome } from '../../core/api/receipt-inspection'

const ready = <T,>(data: T): ContextSource<T> => ({ phase: 'ready', data, error: null, refreshing: false })
const input: OperationalContextInput = {
  identity: contextPrincipal,
  status: ready(contextStatus),
  balances: ready(staleBalances),
}
function renderContext(overrides: Partial<OperationalContextInput> = {}) {
  const context = deriveOperationalContext({ ...input, ...overrides })
  return render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)
}

describe('ContextBar', () => {
  it('renders backend bot, account, market, identity, and action availability from its model', () => {
    renderContext({ status: ready({ ...contextStatus, markets: [{ admitted: false, connector: 'okx', inventory_skew: null,
      market: 'spot', pair: 'LIFE-USDT', quote_guard: null, ready: false, reason: 'not_started',
      reference_price: null, spread_policy: null }] }) })
    expect(screen.getByText('bot-a')).toBeInTheDocument()
    expect(screen.getByText(/fixture-account/)).toBeInTheDocument()
    expect(screen.getByText('okx · spot · LIFE-USDT')).toBeInTheDocument()
    expect(screen.getByText('Operator · google:operator')).toBeInTheDocument()
    expect(screen.getByText('start: bị chặn')).toBeInTheDocument()
    expect(screen.getByText('Session is active.')).toBeInTheDocument()
  })

  it('shows missing status and balance sources independently', () => {
    renderContext({
      status: { phase: 'error', error: new Error('offline'), refreshing: false },
      balances: { phase: 'error', error: new Error('offline'), refreshing: false },
    })
    expect(screen.getByText('Không đọc được trạng thái')).toBeInTheDocument()
    expect(screen.getByText('Không đọc được trạng thái tài khoản')).toBeInTheDocument()
    expect(screen.getByText('Chưa đọc được khả năng thao tác; hãy tải lại trạng thái.')).toBeInTheDocument()
  })

  it('shows status source error while balances source is ready', () => {
    renderContext({
      status: { phase: 'error', error: new Error('status down'), refreshing: false },
      balances: ready(staleBalances),
    })
    expect(screen.getByText('Không đọc được trạng thái')).toBeInTheDocument()
    expect(screen.getByText(/fixture-account/)).toBeInTheDocument()
    expect(screen.queryByText('Không đọc được trạng thái tài khoản')).not.toBeInTheDocument()
  })

  it('shows balances source error while status source is ready', () => {
    renderContext({
      status: ready(contextStatus),
      balances: { phase: 'error', error: new Error('balances down'), refreshing: false },
    })
    expect(screen.getByText('bot-a')).toBeInTheDocument()
    expect(screen.getByText('Không đọc được trạng thái tài khoản')).toBeInTheDocument()
    expect(screen.queryByText('Không đọc được trạng thái')).not.toBeInTheDocument()
    expect(screen.getByText('stop: được phép')).toBeInTheDocument()
  })

  it('retains last-known bot context with an error label and does not claim capabilities', () => {
    renderContext({ status: { phase: 'error', data: contextStatus, error: new Error('offline'), refreshing: false } })
    expect(screen.getByText('bot-a')).toBeInTheDocument()
    expect(screen.getByText(/Dữ liệu được giữ lại từ lần đọc trước/)).toBeInTheDocument()
    expect(screen.getByText('Chưa đọc được khả năng thao tác; hãy tải lại trạng thái.')).toBeInTheDocument()
  })

  it('shows unknown environment and unverified account from the source model', () => {
    const account = staleBalances.accounts[0]
    renderContext({
      status: ready({ ...contextStatus, process_identity: null, owner_identity: null }),
      balances: ready({ ...staleBalances, accounts: [{ ...account, account_id: null, identity_verified: false, environment: 'unknown', balances: [] }] }),
    })
    expect(screen.getAllByText('Chưa xác định')).toHaveLength(2)
    expect(screen.getByText('Chưa xác minh danh tính tài khoản')).toBeInTheDocument()
  })

  it('shows a mismatch only for a verified current account observation', () => {
    const account = staleBalances.accounts[0]
    renderContext({
      balances: ready({ ...staleBalances, owner_available: true, accounts: [{ ...account, environment: 'live',
        balances: account.balances.map((balance) => ({ ...balance, stale: false, present_in_latest_snapshot: true })) }] }),
    })
    expect(screen.getByRole('alert')).toHaveTextContent('các nguồn nhận diện môi trường từ backend không khớp')
  })

  it('shows every account without selecting a trading account', () => {
    const account = staleBalances.accounts[0]
    renderContext({ balances: ready({ ...staleBalances, owner_available: true, accounts: [
      { ...account, environment: 'demo' },
      { ...account, account_id: 'second-account', environment: 'live' },
    ] }) })
    expect(screen.getByText('Có 2 tài khoản được backend trả về')).toBeInTheDocument()
    expect(screen.getByText('fixture-account · Demo')).toBeInTheDocument()
    expect(screen.getByText('second-account · Live')).toBeInTheDocument()
  })

  it('does not attribute a process/owner mismatch to a nonexistent exchange account', () => {
    renderContext({ status: ready({ ...contextStatus,
      owner_identity: { ...contextStatus.owner_identity!, trading_environment: 'live' } }),
    balances: ready({ ...staleBalances, accounts: [] }) })
    expect(screen.getByRole('alert')).not.toHaveTextContent('tài khoản đã xác minh')
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
    expect(screen.getAllByText('Dữ liệu cũ').length).toBeGreaterThanOrEqual(1)
  })

  it('does not label a failed status refresh fresh', () => {
    const context = deriveOperationalContext({
      identity: contextPrincipal,
      status: { phase: 'error', data: contextStatus, error: new Error('offline'), refreshing: false },
      balances: { phase: 'ready', data: staleBalances, error: null, refreshing: false },
    })
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)
    expect(screen.queryByText('Mới', { exact: true })).not.toBeInTheDocument()
    expect(screen.getAllByText('Dữ liệu cũ').length).toBeGreaterThanOrEqual(1)
  })

  it('discloses stale account observations even for a single account', () => {
    const context = deriveOperationalContext({
      identity: contextPrincipal,
      status: { phase: 'ready', data: contextStatus, error: null, refreshing: false },
      balances: { phase: 'ready', data: staleBalances, error: null, refreshing: false },
    })
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)
    expect(context.accounts[0].observation).toBe('stale')
    expect(screen.getByText('Dữ liệu cũ', { exact: true })).toBeInTheDocument()
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
    const { rerender } = render(<ContextBar context={unknownCtx} apiBaseUrl="/api/v1/admin" />)
    expect(screen.getByText('Chưa có mốc thời gian')).toBeInTheDocument()
    expect(screen.getByText('Chưa xác định')).toBeInTheDocument()

    const mismatchCtx = deriveOperationalContext({
      ...input,
      status: ready({
        ...contextStatus,
        observation: { observed_at: '2026-11-10T12:00:00Z', reason_code: null, stale: false, session_matches: false },
      }),
    })
    rerender(<ContextBar context={mismatchCtx} apiBaseUrl="/api/v1/admin" />)
    expect(screen.getByText('Phiên không khớp')).toBeInTheDocument()

    const freshCtx = deriveOperationalContext({
      ...input,
      status: ready({
        ...contextStatus,
        observation: { observed_at: '2026-11-10T12:00:00Z', reason_code: null, stale: false, session_matches: true },
      }),
    })
    rerender(<ContextBar context={freshCtx} apiBaseUrl="/api/v1/admin" />)
    expect(screen.getByText('Mới')).toBeInTheDocument()
  })

  it('renders tracked operation references and allows keyboard selection and inspection', async () => {
    const user = userEvent.setup()
    const context = deriveOperationalContext(input)
    const longId = 'cmd-1234567890abcdef1234567890abcdef'
    const { rerender } = render(
      <ContextBar
        context={context}
        apiBaseUrl="/api/v1/admin"
        trackedReceipts={[
          { kind: 'command', id: longId },
          { kind: 'recovery', id: 'rec-0987654321fedcba' },
        ]}
      />
    )
    expect(screen.getByText(/Thao tác trước đó đang theo dõi/)).toBeInTheDocument()
    const cmdBtn = screen.getByRole('button', { name: /command: cmd-12345678…/ })
    const recBtn = screen.getByRole('button', { name: /recovery: rec-09876543…/ })
    expect(cmdBtn).toBeInTheDocument()
    expect(recBtn).toBeInTheDocument()
    expect(cmdBtn).toHaveAttribute('aria-pressed', 'false')

    await user.click(cmdBtn)
    expect(cmdBtn).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Chi tiết thao tác đang theo dõi')).toBeInTheDocument()
    expect(screen.getByText(longId)).toBeInTheDocument()

    rerender(
      <ContextBar
        context={context}
        apiBaseUrl="/api/v1/admin"
        trackedReceipts={[
          { kind: 'command', id: longId },
          { kind: 'recovery', id: 'rec-0987654321fedcba' },
        ]}
        selectedReceipt={{ kind: 'command', id: longId }}
        receiptInspection={{
          reference: { kind: 'command', id: longId },
          phase: 'ready',
          status: 'succeeded',
          detail: 'Thao tác hoàn tất',
          error: null,
          outcome: { kind: 'command', action: 'stop', sessionId: 'session-1', error: null },
        }}
      />
    )
    expect(screen.getByText('Tiến trình receipt: succeeded')).toBeInTheDocument()
    expect(screen.getByText(/không xác nhận trạng thái lệnh hoặc vị thế hiện tại/)).toBeInTheDocument()

    const closeBtn = screen.getByRole('button', { name: 'Đóng' })
    await user.click(closeBtn)
  })

  function renderReceipt(status: string, outcome: ReceiptOutcome) {
    const reference = { kind: outcome.kind, id: 'receipt-1' } as const
    const inspection: TrackedReceiptInspection = { reference, phase: 'ready', status, error: null, outcome }
    render(<ContextBar context={deriveOperationalContext(input)} apiBaseUrl="/api/v1/admin"
      trackedReceipts={[reference]} selectedReceipt={reference} receiptInspection={inspection} />)
  }

  it('keeps a succeeded reconciliation receipt distinct from mismatch outcome', () => {
    renderReceipt('succeeded', { kind: 'reconciliation', state: 'mismatch', reasons: ['missing order'] })
    expect(screen.getByText('Tiến trình receipt: succeeded')).toBeInTheDocument()
    expect(screen.getByText('Kết quả đối soát: Có chênh lệch')).toBeInTheDocument()
    expect(screen.getByText('missing order')).toBeInTheDocument()
  })

  it('shows a succeeded but blocked recovery as not recoverable', () => {
    renderReceipt('succeeded', { kind: 'recovery', action: 'recovery_check', stage: 'blocked', canRecover: false,
      blockers: [{ code: 'owner_unavailable', detail: 'Owner is offline.', next_action: 'wait' }] })
    expect(screen.getByText('Tiến trình receipt: succeeded')).toBeInTheDocument()
    expect(screen.getByText('Backend chưa xác nhận có thể phục hồi.')).toBeInTheDocument()
    expect(screen.getByText('owner_unavailable')).toBeInTheDocument()
    expect(screen.getByText(/Owner is offline./)).toBeInTheDocument()
  })

  it('shows recovery success only when succeeded and stage is recovered', () => {
    renderReceipt('succeeded', { kind: 'recovery', action: 'recovery_reset', stage: 'recovered', canRecover: false, blockers: [] })
    expect(screen.getByText('Đã phục hồi theo receipt.')).toBeInTheDocument()
  })

  it('keeps pending receipt progress separate from the final credential stage', () => {
    renderReceipt('applying', { kind: 'credential', action: 'credentials_disconnect', stage: 'canceling_orders',
      operatorActionRequired: false, reasonCode: null, error: null })
    expect(screen.getByText('Tiến trình receipt: applying')).toBeInTheDocument()
    expect(screen.getByText('Giai đoạn')).toBeInTheDocument()
    expect(screen.getByText('canceling_orders')).toBeInTheDocument()
    expect(screen.getByText(/chưa xác nhận trạng thái kết nối hiện tại/)).toBeInTheDocument()
  })

  it('shows receipt read errors separately from operation outcome', () => {
    const reference = { kind: 'command', id: 'cmd-fail-read' } as const
    const inspection: TrackedReceiptInspection = { reference, phase: 'error', error: new Error('receipt unavailable') }
    render(<ContextBar context={deriveOperationalContext(input)} apiBaseUrl="/api/v1/admin" trackedReceipts={[reference]}
      selectedReceipt={reference} receiptInspection={inspection} />)
    expect(screen.getByText('receipt unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/Kết quả đối soát/)).not.toBeInTheDocument()
  })

  it('renders technical diagnostics details disclosure', () => {
    const context = deriveOperationalContext(input)
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)
    expect(screen.getByText('Chi tiết kỹ thuật & chẩn đoán')).toBeInTheDocument()
    expect(screen.getByText('Control Version:')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('displays non-shifting refreshing overlays and accessible status when refreshing', () => {
    const context = deriveOperationalContext({
      ...input,
      status: { ...input.status, refreshing: true },
      balances: { ...input.balances, refreshing: true },
    })
    render(<ContextBar context={context} apiBaseUrl="/api/v1/admin" />)

    // Global header indicator
    expect(screen.getByText('Đang đồng bộ…')).toBeInTheDocument()

    // Card overlays
    expect(screen.getByText('Đang đồng bộ bot & số dư…')).toBeInTheDocument()
    expect(screen.getByText('Đang cập nhật quyền…')).toBeInTheDocument()

    // Screen reader live status
    expect(screen.getByRole('status')).toHaveTextContent('Đang cập nhật trạng thái bot.')
    expect(screen.getByRole('status')).toHaveTextContent('Đang cập nhật số dư tài khoản.')
  })
})
