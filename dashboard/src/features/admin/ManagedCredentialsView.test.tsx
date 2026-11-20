import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ManagedCredentialsView } from './ManagedCredentialsView'
import type { FormFeedbackModel } from '../../core/api/operation-feedback'

describe('ManagedCredentialsView', () => {
  const sampleSlot = {
    exchange: 'okx',
    connector: 'okx',
    descriptor: { exchange: 'okx', connector: 'okx', site: 'OKX Spot', environment: 'live' as const },
    fields: [
      { name: 'api_key', label: 'API Key', secret: false, required: true },
      { name: 'secret', label: 'Secret Key', secret: true, required: true },
    ],
    slot: { configured: true, status: 'verified', label: 'OKX Live', changed_at: '2026-11-10T00:00:00Z', changed_by: 'admin', observed_at: '2026-11-10T00:00:00Z', detail: null },
    source: 'postgres_kms' as const,
    connectionState: 'connected' as const,
    decisions: {
      set: { allowed: true, detail: 'Ready to set', reasonCode: null },
      disconnect: { allowed: true, detail: 'Ready to disconnect', reasonCode: null },
      retry: { allowed: false, detail: 'No cleanup', reasonCode: null },
      repair: { allowed: false, detail: 'No cleanup', reasonCode: null },
    },
    cleanup: null,
    disconnectedProof: false,
    stopAcknowledged: true,
  } as unknown as Parameters<typeof ManagedCredentialsView>[0]['slots'][number]

  const renderView = (slots: Parameters<typeof ManagedCredentialsView>[0]['slots'][number][]) =>
    render(
      <ManagedCredentialsView
        slots={slots}
        locks={[]}
        canManage={true}
        isLoading={false}
        isSubmitting={false}
        onRefresh={vi.fn()}
        onSetCredentials={vi.fn()}
        onRetryCleanup={vi.fn()}
        onRepairCleanup={vi.fn()}
        onReviewUnknown={vi.fn()}
        onOpenDisconnect={vi.fn()}
      />,
    )

  it('renders credential slots with advertised input fields and friendly connection labels', () => {
    renderView([sampleSlot])

    expect(screen.getByText('okx · okx')).toBeInTheDocument()
    expect(screen.getByText('Connected')).toBeInTheDocument()
    expect(screen.getByText('postgres_kms')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Disconnect/ })).toBeInTheDocument()
  })

  it.each([
    ['loaded', 'Credentials loaded and verified.'],
    ['failed', 'Credentials were not loaded.'],
    ['incomplete', 'The operation ended without confirming that credentials loaded.'],
  ] as const)('presents Set completion state %s accurately', (state, message) => {
    renderView([{ ...sampleSlot, setOutcome: { state, detail: null } }])
    expect(screen.getByText(message)).toBeInTheDocument()
  })

  it.each([
    ['not_configured', 'Not configured'],
    ['checking', 'Checking connection'],
    ['unknown', 'Checking connection'],
    ['disconnecting', 'Disconnecting'],
    ['action_required', 'Action required'],
    ['disconnected', 'Disconnected'],
    ['failed', 'Connection failed'],
    ['unavailable', 'Status unavailable'],
  ] as const)('maps connection state %s to operator label %s', (state, label) => {
    renderView([{ ...sampleSlot, connectionState: state, disconnectedProof: state === 'disconnected' }])
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('waits for root removal proof before labeling a disconnected slot', () => {
    renderView([{ ...sampleSlot, connectionState: 'disconnected', disconnectedProof: false }])
    expect(screen.queryByText('Disconnected', { exact: true })).not.toBeInTheDocument()
    expect(screen.getByText('Waiting for removal verification')).toBeInTheDocument()
  })

  it('shows Managed by deployment instead of the raw environment source', () => {
    renderView([{ ...sampleSlot, source: 'environment' as const, connectionState: 'connected' as const }])
    expect(screen.getByText('Managed by deployment')).toBeInTheDocument()
    expect(screen.queryByText('environment')).not.toBeInTheDocument()
  })

  it('shows the friendly cleanup stage with reason code kept in Details', () => {
    const slotWithCleanup = {
      ...sampleSlot,
      cleanup: {
        id: 'cleanup-1',
        stage: 'waiting_for_dependency',
        label: 'Waiting for order verification',
        blockedStage: null,
        reasonCode: 'cleanup_orders_pending',
        nextRetryAt: '2026-11-16T00:01:00Z',
        finalProofs: { ordersTerminalVerified: false, positionsFlatVerified: true, ledgerConfirmed: false, consumersDetached: true },
        complete: false,
      },
    } as unknown as typeof sampleSlot
    renderView([slotWithCleanup])
    expect(screen.getByText(/Waiting for order verification/)).toBeInTheDocument()
    expect(screen.getByText(/An uncertain order is still being verified on the exchange/)).toBeInTheDocument()
    expect(screen.getByText(/Next retry: .+ UTC/)).toBeInTheDocument()
    // The raw stage/reason stay collapsed inside Details; expanding confirms they are preserved
    fireEvent.click(screen.getByText('Details'))
    expect(screen.getByText(/stage: waiting_for_dependency/)).toBeInTheDocument()
    expect(screen.getByText(/reason_code: cleanup_orders_pending/)).toBeInTheDocument()
    expect(screen.getByText(/next_retry_at: 2026-11-16T00:01:00Z/)).toBeInTheDocument()
  })

  it('renders inline error on credential field from feedbackModel when form is open', () => {
    const feedbackModel: FormFeedbackModel = {
      feedback: {
        surface: 'inline',
        code: 'invalid_credentials',
        requestId: 'req-cred-999',
        fields: [],
        retry: 'fix_request',
        retryAt: null,
        outcomeUnknown: false,
      },
      inlineErrors: {
        api_key: ['invalid_api_key_format'],
      },
      globalFields: [],
    }

    render(
      <ManagedCredentialsView
        slots={[sampleSlot]}
        locks={[]}
        canManage={true}
        isLoading={false}
        isSubmitting={false}
        onRefresh={vi.fn()}
        onSetCredentials={vi.fn()}
        onRetryCleanup={vi.fn()}
        onRepairCleanup={vi.fn()}
        onReviewUnknown={vi.fn()}
        onOpenDisconnect={vi.fn()}
        feedbackModel={feedbackModel}
      />,
    )

    // Open the set credentials form
    const openBtn = screen.getByRole('button', { name: /Set okx credentials/ })
    fireEvent.click(openBtn)

    // Inline error on advertised field
    expect(screen.getByText('invalid_api_key_format')).toBeInTheDocument()

    // Diagnostics feedback
    expect(screen.getByText('Code: invalid_credentials')).toBeInTheDocument()
  })

  it('shows the repair decision detail when repairing a cleanup key', () => {
    const repairSlot = {
      ...sampleSlot,
      connectionState: 'action_required' as const,
      decisions: {
        ...sampleSlot.decisions,
        repair: { allowed: true, detail: 'Provide a working key for the same account to finish cleanup.', reasonCode: null },
      },
      cleanup: {
        id: 'cleanup-1',
        stage: 'action_required',
        label: 'Action required',
        blockedStage: 'checking_account',
        reasonCode: 'credential_invalid',
        nextRetryAt: null,
        finalProofs: { ordersTerminalVerified: false, positionsFlatVerified: false, ledgerConfirmed: false, consumersDetached: false },
        complete: false,
      },
    } as unknown as typeof sampleSlot

    renderView([repairSlot])
    fireEvent.click(screen.getByRole('button', { name: 'Repair cleanup key' }))
    expect(screen.getByText('Repair cleanup credentials')).toBeInTheDocument()
    expect(screen.getByText('Provide a working key for the same account to finish cleanup.')).toBeInTheDocument()
    expect(screen.queryByText('Ready to set')).not.toBeInTheDocument()
  })

  it('shows why set credentials is locked without requiring the form to open, and keeps submit disabled', () => {
    const lockedSlot = {
      ...sampleSlot,
      decisions: {
        ...sampleSlot.decisions,
        set: { allowed: false, detail: 'Bot must be stopped and acknowledged by the backend before changing credentials.', reasonCode: 'bot_running' },
      },
    } as unknown as typeof sampleSlot

    renderView([lockedSlot])

    // The lock reason is visible before opening the form
    expect(screen.getByText(/Set credentials is locked/)).toBeInTheDocument()
    expect(screen.getByText(/Bot must be stopped and acknowledged/)).toBeInTheDocument()

    // The toggle still opens the form so the operator can read the full context
    const openBtn = screen.getByRole('button', { name: /Set okx credentials/ })
    expect(openBtn).not.toBeDisabled()
    fireEvent.click(openBtn)

    expect(screen.getByText(/Submitting is locked/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit set' })).toBeDisabled()
  })

  it('explains why no input form is offered for deployment-managed slots', () => {
    renderView([{ ...sampleSlot, fields: [], source: 'environment' as const }])

    expect(screen.queryByRole('button', { name: /Set okx credentials/ })).not.toBeInTheDocument()
    expect(screen.getByText(/backend must run in postgres_kms mode/)).toBeInTheDocument()
  })

  it('opens the disconnect flow even while locked, with the reason visible', () => {
    const onOpenDisconnect = vi.fn()
    const lockedSlot = {
      ...sampleSlot,
      decisions: {
        ...sampleSlot.decisions,
        disconnect: { allowed: false, detail: 'Bot must be stopped and confirmed by the backend.', reasonCode: 'bot_running' },
      },
    } as unknown as typeof sampleSlot

    render(
      <ManagedCredentialsView
        slots={[lockedSlot]}
        locks={[]}
        canManage={true}
        isLoading={false}
        isSubmitting={false}
        onRefresh={vi.fn()}
        onSetCredentials={vi.fn()}
        onRetryCleanup={vi.fn()}
        onRepairCleanup={vi.fn()}
        onReviewUnknown={vi.fn()}
        onOpenDisconnect={onOpenDisconnect}
      />,
    )

    expect(screen.getByText(/Disconnect is locked/)).toBeInTheDocument()
    const disconnectBtn = screen.getByRole('button', { name: /Disconnect/ })
    expect(disconnectBtn).not.toBeDisabled()
    fireEvent.click(disconnectBtn)
    expect(onOpenDisconnect).toHaveBeenCalledWith('okx', 'okx')
  })

  it('opens the repair form even while repair is locked, with submit disabled', () => {
    const lockedRepairSlot = {
      ...sampleSlot,
      connectionState: 'action_required' as const,
      decisions: {
        ...sampleSlot.decisions,
        repair: { allowed: false, detail: 'Repair needs a verified cleanup root receipt first.', reasonCode: 'cleanup_not_ready' },
      },
      cleanup: {
        id: 'cleanup-1',
        stage: 'action_required',
        label: 'Action required',
        blockedStage: 'checking_account',
        reasonCode: 'credential_invalid',
        nextRetryAt: null,
        finalProofs: { ordersTerminalVerified: false, positionsFlatVerified: false, ledgerConfirmed: false, consumersDetached: false },
        complete: false,
      },
    } as unknown as typeof sampleSlot

    renderView([lockedRepairSlot])

    expect(screen.getByText(/Repair cleanup key is locked/)).toBeInTheDocument()
    const repairBtn = screen.getByRole('button', { name: 'Repair cleanup key' })
    expect(repairBtn).not.toBeDisabled()
    fireEvent.click(repairBtn)

    expect(screen.getByText('Repair cleanup credentials')).toBeInTheDocument()
    expect(screen.getByText(/Submitting is locked/)).toBeInTheDocument()
    expect(screen.getByText(/Repair needs a verified cleanup root receipt first\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit repair' })).toBeDisabled()
  })
})
